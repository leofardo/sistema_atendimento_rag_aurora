"""Cache semântico: pergunta parecida com uma já respondida reutiliza a resposta.

"Qual o prazo para devolver?" e "quanto tempo tenho pra devolver?" são a mesma
pergunta. O vetor da MENSAGEM do cliente sai na mesma requisição do vetor da
busca (custo ~20 tokens): se a similaridade com uma pergunta respondida passar
de CACHE_SIMILARIDADE (0,95), a resposta sai daqui e a chamada do modelo de
resposta — a mais cara do turno — nem acontece.

Só entram no cache respostas GENÉRICAS e sem contexto: primeira pergunta da
conversa, sem pedido nem data do cliente. A chave é a mensagem original, não a
consulta reescrita — a reescrita apaga detalhes ("air fryer") que a resposta
usou, e o teste real mostrou essa resposta servida a quem não falou de air
fryer. Quem decide é `pode_cachear` em app.servicos.atendente.

O cache é zerado quando a base é reindexada com mudanças: resposta baseada
em política velha não pode sobreviver à política nova.
"""

import json
import time
import uuid
from typing import Any

from app.nucleo.configuracao import similaridade_cache, validade_cache_horas
from app.rag.indice import obter_indice

COLECAO_CACHE = "cache_respostas"


def buscar(vetor: list[float]) -> dict[str, Any] | None:
    colecao = obter_indice().colecao(COLECAO_CACHE)
    if colecao.count() == 0:
        return None
    achado = colecao.query(query_embeddings=[vetor], n_results=1, include=["metadatas", "distances"])
    if not achado["ids"][0]:
        return None
    similaridade = 1 - achado["distances"][0][0]
    meta = achado["metadatas"][0][0]
    if similaridade < similaridade_cache():
        return None
    if time.time() - float(meta["criado_em"]) > validade_cache_horas() * 3600:
        colecao.delete(ids=[achado["ids"][0][0]])
        return None
    return {
        "resposta": meta["resposta"],
        "fontes": json.loads(meta["fontes"]),
        "perguntaOriginal": meta["pergunta"],
        "similaridade": round(similaridade, 4),
    }


def guardar(vetor: list[float], pergunta: str, resposta: str, fontes: list[dict[str, Any]]) -> None:
    obter_indice().colecao(COLECAO_CACHE).add(
        ids=[uuid.uuid4().hex],
        embeddings=[vetor],
        documents=[pergunta],
        metadatas=[{
            "pergunta": pergunta,
            "resposta": resposta,
            "fontes": json.dumps(fontes, ensure_ascii=False),
            "criado_em": time.time(),
        }],
    )


def listar() -> list[dict[str, Any]]:
    """Tudo que está no cache, da resposta mais nova para a mais velha."""
    dados = obter_indice().colecao(COLECAO_CACHE).get(include=["metadatas"])
    itens = [
        {"id": i, "pergunta": m["pergunta"], "resposta": m["resposta"], "fontes": json.loads(m["fontes"]),
         "criadoEm": float(m["criado_em"]),
         "expiraEmHoras": max(0.0, validade_cache_horas() - (time.time() - float(m["criado_em"])) / 3600)}
        for i, m in zip(dados["ids"], dados["metadatas"])
    ]
    return sorted(itens, key=lambda item: item["criadoEm"], reverse=True)


def apagar(item_id: str) -> None:
    obter_indice().colecao(COLECAO_CACHE).delete(ids=[item_id])


def limpar() -> int:
    colecao = obter_indice().colecao(COLECAO_CACHE)
    ids = colecao.get(include=[])["ids"]
    if ids:
        colecao.delete(ids=ids)
    return len(ids)


def total() -> int:
    return obter_indice().colecao(COLECAO_CACHE).count()
