"""Índice da base de conhecimento: busca HÍBRIDA (vetorial + BM25) com RRF.

Por que duas buscas:
- A VETORIAL (embedding + ChromaDB) entende sentido: "me arrependi da compra"
  encontra o art. 49 mesmo sem a palavra "desistir".
- A LEXICAL (BM25) acerta termo exato, onde embedding é fraco: "art. 49",
  "Decreto 11.034", "Aurora+", "0800", número de lei.
As duas listas são unidas por RECIPROCAL RANK FUSION: cada trecho ganha
1/(60 + posição) em cada lista e soma. Não precisa calibrar pesos entre
escalas diferentes (cosseno vs. pontuação BM25) — só a posição importa.

Depois da fusão, um FILTRO DE RELEVÂNCIA corta trecho com cosseno baixo: ele
entraria no prompt, custaria tokens e só confundiria a resposta.

ChromaDB guarda os vetores em disco (dados/indice/chroma) com índice HNSW. A
reindexação é INCREMENTAL: o id do trecho é um hash do conteúdo, então só
trecho novo ou alterado paga embedding.
"""

import math
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

import chromadb
from rank_bm25 import BM25Okapi

from app.nucleo.configuracao import (
    PASTA_INDICE,
    modelo_embedding,
    peso_rrf_bm25,
    similaridade_minima,
    trechos_por_resposta,
)
from app.rag.documentos import Trecho, todos_os_trechos
from app.servicos import openai_cliente

COLECAO = "base_conhecimento"
CANDIDATOS = 20  # quantos cada busca devolve antes da fusão
K_RRF = 60

STOPWORDS = set(
    """a o e é de da do das dos em no na nos nas um uma uns umas para por com sem que se ao aos à às ou
    como mais mas foi ser são tem ter há eu meu minha meus minhas você vocês seu sua seus suas ele ela
    eles elas isso isto esse essa este esta qual quais quando onde já não sim me te lhe nos vos pelo pela
    pelos pelas sobre entre até também só muito quero queria gostaria posso pode preciso""".split()
)


@dataclass
class Resultado:
    trecho_id: str
    fonte: str
    referencia: str
    caminho: str
    texto: str
    url: str
    similaridade: float
    posicao_vetorial: int | None
    posicao_bm25: int | None
    pontuacao_rrf: float


def normalizar(texto: str) -> list[str]:
    """Tokens para o BM25: minúsculas, sem acento, sem stopword.

    Sem acento porque cliente escreve "devolucao", "garantia estendida",
    "arrependimento" de todo jeito. Número fica ("49", "11034") — é justamente
    onde a busca lexical ganha da vetorial.
    """
    sem_acento = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    sem_acento = re.sub(r"(\d)\.(\d)", r"\1\2", sem_acento)  # 11.034 -> 11034
    return [t for t in re.findall(r"[a-z0-9]+", sem_acento) if t not in STOPWORDS and len(t) > 1]


# Como o cliente cita cada lei. Usado para ir DIRETO ao artigo citado.
APELIDOS_DE_LEI = {
    "CDC": r"\bcdc\b|codigo de defesa|8\.?078",
    "Decreto SAC": r"11\.?034|\bsac\b",
    "Decreto Comércio Eletrônico": r"7\.?962|comercio eletronico",
    "Lei Atendimento Prioritário": r"10\.?048|atendimento prioritario",
}
CITACAO_DE_ARTIGO = re.compile(r"\bart(?:igo)?s?\.?\s*(\d+)")


def referencias_explicitas(texto: str) -> list[tuple[str | None, str]]:
    """[(fonte ou None, "Art. N")] para cada artigo citado com número.

    "art. 49 do CDC" não precisa de busca por similaridade: o código sabe
    exatamente qual trecho é. A avaliação mostrou que "artigo 10 do decreto
    11.034" nem aparecia entre os 20 candidatos da busca vetorial.
    """
    normal = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    artigos = CITACAO_DE_ARTIGO.findall(normal)
    if not artigos:
        return []
    fontes = [fonte for fonte, padrao in APELIDOS_DE_LEI.items() if re.search(padrao, normal)]
    return [(fonte, f"Art. {numero}") for numero in artigos for fonte in (fontes or [None])]


def fundir_rrf(ids_vetoriais: list[str], ids_bm25: list[str], peso_bm25: float | None = None) -> dict[str, float]:
    """Reciprocal Rank Fusion ponderado: peso/(60 + posição) em cada lista, somado.

    Peso igual nas duas listas PIOROU a busca nesta base (scripts/avaliar_rag.py):
    o embedding acerta quase tudo sozinho e o BM25 empurrava trecho errado para
    cima. Com peso menor, o BM25 só desempata e resgata o termo exato.
    """
    peso_bm25 = peso_rrf_bm25() if peso_bm25 is None else peso_bm25
    pontuacao: dict[str, float] = {}
    for peso, lista in ((1.0, ids_vetoriais), (peso_bm25, ids_bm25)):
        for posicao, trecho_id in enumerate(lista):
            pontuacao[trecho_id] = pontuacao.get(trecho_id, 0) + peso / (K_RRF + posicao + 1)
    return pontuacao


def _cosseno(a: list[float], b: list[float]) -> float:
    produto = sum(x * y for x, y in zip(a, b))
    norma = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return produto / norma if norma else 0.0


class Indice:
    def __init__(self) -> None:
        PASTA_INDICE.mkdir(parents=True, exist_ok=True)
        self._cliente = chromadb.PersistentClient(path=str(PASTA_INDICE / "chroma"))
        self._bm25: BM25Okapi | None = None
        self._ids_bm25: list[str] = []

    # --- coleções -------------------------------------------------------
    def colecao(self, nome: str = COLECAO) -> Any:
        # embedding_function=None: os vetores são calculados por nós (OpenAI),
        # assim o Chroma não baixa modelo local nenhum. "cosine" porque os
        # embeddings da OpenAI são normalizados para comparar por ângulo.
        return self._cliente.get_or_create_collection(
            name=nome, embedding_function=None, metadata={"hnsw:space": "cosine"}
        )

    def total(self) -> int:
        return self.colecao().count()

    # --- indexação ------------------------------------------------------
    async def indexar(self) -> dict[str, Any]:
        """Sincroniza o Chroma com os documentos. Só paga embedding do que mudou."""
        inicio = time.monotonic()
        trechos = todos_os_trechos()
        colecao = self.colecao()
        existentes = set(colecao.get(include=[])["ids"])
        atuais = {trecho.id: trecho for trecho in trechos}

        novos = [trecho for trecho_id, trecho in atuais.items() if trecho_id not in existentes]
        removidos = [trecho_id for trecho_id in existentes if trecho_id not in atuais]

        tokens = 0
        if novos:
            vetores, tokens = await openai_cliente.embeddings([t.texto_indexado for t in novos])
            colecao.upsert(
                ids=[t.id for t in novos],
                embeddings=vetores,
                documents=[t.texto for t in novos],
                metadatas=[_metadados(t) for t in novos],
            )
        if removidos:
            colecao.delete(ids=removidos)

        self._bm25 = None  # reconstruído na próxima busca
        return {
            "trechos": len(atuais),
            "novos": len(novos),
            "removidos": len(removidos),
            "reaproveitados": len(atuais) - len(novos),
            "tokensEmbedding": tokens,
            "modelo": modelo_embedding(),
            "duracaoMs": int((time.monotonic() - inicio) * 1000),
        }

    def listar_trechos(self) -> list[dict[str, Any]]:
        dados = self.colecao().get(include=["documents", "metadatas"])
        return [{"id": i, "texto": d, **m} for i, d, m in zip(dados["ids"], dados["documents"], dados["metadatas"])]

    # --- busca ----------------------------------------------------------
    def _garantir_bm25(self) -> None:
        if self._bm25 is not None:
            return
        dados = self.colecao().get(include=["documents", "metadatas"])
        self._ids_bm25 = dados["ids"]
        corpus = [normalizar(f"{m['caminho']} {d}") for d, m in zip(dados["documents"], dados["metadatas"])]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def _buscar_bm25(self, consulta: str) -> list[str]:
        self._garantir_bm25()
        termos = normalizar(consulta)
        if not self._bm25 or not termos:
            return []
        pontos = self._bm25.get_scores(termos)
        ordem = sorted(range(len(pontos)), key=lambda i: pontos[i], reverse=True)
        return [self._ids_bm25[i] for i in ordem[:CANDIDATOS] if pontos[i] > 0]

    def _ids_citados(self, textos: list[str]) -> list[str]:
        """Trechos dos artigos citados explicitamente, em ordem de citação."""
        citados: list[str] = []
        for texto in textos:
            for fonte, referencia in referencias_explicitas(texto):
                filtro: dict[str, Any] = {"referencia": referencia}
                if fonte:
                    filtro = {"$and": [{"fonte": fonte}, {"referencia": referencia}]}
                ids = self.colecao().get(where=filtro, include=[])["ids"]
                if not ids and not fonte:
                    continue
                if not ids:  # artigo longo: "Art. 18 (parte 1/2)"
                    candidatos = self.colecao().get(where={"fonte": fonte}, include=["metadatas"])
                    ids = [i for i, m in zip(candidatos["ids"], candidatos["metadatas"])
                           if m["referencia"].startswith(referencia + " (")]
                citados += [i for i in ids if i not in citados]
        return citados

    def buscar(
        self, consulta: str, vetor: list[float], k: int | None = None, texto_original: str = ""
    ) -> list[Resultado]:
        k = k or trechos_por_resposta()
        colecao = self.colecao()
        if colecao.count() == 0:
            return []

        vetorial = colecao.query(
            query_embeddings=[vetor], n_results=min(CANDIDATOS, colecao.count()),
            include=["documents", "metadatas", "distances"],
        )
        ids_vetoriais = vetorial["ids"][0]
        similaridades = {i: 1 - d for i, d in zip(ids_vetoriais, vetorial["distances"][0])}
        lexical = self._buscar_bm25(consulta)

        pontuacao = fundir_rrf(ids_vetoriais, lexical)
        # Artigo citado pelo número vai na frente de tudo, sem passar pelo limiar.
        citados = self._ids_citados([texto_original, consulta])[:k]
        for trecho_id in citados:
            pontuacao[trecho_id] = 1.0 + pontuacao.get(trecho_id, 0)

        # Quem veio só do BM25 não tem cosseno ainda: busca os vetores e calcula.
        faltando = [i for i in pontuacao if i not in similaridades]
        if faltando:
            extras = colecao.get(ids=faltando, include=["embeddings"])
            for trecho_id, embedding in zip(extras["ids"], extras["embeddings"]):
                similaridades[trecho_id] = _cosseno(vetor, list(embedding))

        minimo = similaridade_minima()
        escolhidos = [
            trecho_id
            for trecho_id in sorted(pontuacao, key=pontuacao.get, reverse=True)
            if trecho_id in citados or similaridades.get(trecho_id, 0) >= minimo
        ][:k]
        if not escolhidos:
            return []

        dados = colecao.get(ids=escolhidos, include=["documents", "metadatas"])
        por_id = {i: (d, m) for i, d, m in zip(dados["ids"], dados["documents"], dados["metadatas"])}
        resultados = []
        for trecho_id in escolhidos:
            texto, meta = por_id[trecho_id]
            resultados.append(Resultado(
                trecho_id=trecho_id,
                fonte=meta["fonte"],
                referencia=meta["referencia"],
                caminho=meta["caminho"],
                texto=texto,
                url=meta.get("url", ""),
                similaridade=round(similaridades[trecho_id], 4),
                posicao_vetorial=ids_vetoriais.index(trecho_id) + 1 if trecho_id in ids_vetoriais else None,
                posicao_bm25=lexical.index(trecho_id) + 1 if trecho_id in lexical else None,
                pontuacao_rrf=round(pontuacao[trecho_id], 5),
            ))
        return resultados


def _metadados(trecho: Trecho) -> dict[str, Any]:
    return {
        "fonte": trecho.fonte,
        "documento": trecho.titulo_documento,
        "referencia": trecho.referencia,
        "caminho": trecho.caminho,
        "url": trecho.url,
        "tipo": trecho.tipo,
        "tokens": trecho.tokens,
    }


_indice: Indice | None = None


def obter_indice() -> Indice:
    global _indice
    if _indice is None:
        _indice = Indice()
    return _indice
