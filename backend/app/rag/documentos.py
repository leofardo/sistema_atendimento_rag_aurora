"""Carregar os documentos e cortar em trechos (chunks).

Estratégia: CHUNKING ESTRUTURAL, não por tamanho fixo.

O curso da Alura compara corte por caracteres, por tokens e semântico. Para
lei e manual de política existe um corte melhor que todos: a própria estrutura
do texto. Um artigo de lei é uma unidade de sentido completa, e é exatamente
o que a resposta precisa citar ("CDC, art. 49"). Cortar a cada 500 caracteres
partiria o art. 18 no meio do § 1º — e o trecho recuperado traria a regra sem
a exceção.

- Lei: um trecho por artigo (com seus §§ e incisos).
- Manual da empresa: um trecho por seção "##".
- Trecho grande demais (> MAXIMO_TOKENS): subdividido por tokens com
  sobreposição (RecursiveCharacterTextSplitter.from_tiktoken_encoder, a mesma
  ferramenta do curso), cada pedaço mantendo o cabeçalho.

E todo trecho ganha um CABEÇALHO DE CONTEXTO ("CDC > Capítulo VI > Seção I >
Art. 49"). O artigo sozinho não diz que é sobre contrato; com o caminho na
frente, o embedding e o BM25 sabem de onde ele veio. Custa ~15 tokens por
trecho e melhora muito a busca.
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.nucleo.configuracao import PASTA_DOCUMENTOS
from app.nucleo.precos import contar_tokens

MAXIMO_TOKENS = 450
SOBREPOSICAO_TOKENS = 60
ARTIGO = re.compile(r"^Art\.\s*(\d+[A-Z-]*)")
SEM_CONTEUDO = re.compile(r"entra em vigor|^Art\.\s*\d+\S*\s+Revoga|^Art\.\s*\d+\S*\s+Fica revogad", re.IGNORECASE)

_divisor = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
    encoding_name="o200k_base",
    chunk_size=MAXIMO_TOKENS,
    chunk_overlap=SOBREPOSICAO_TOKENS,
    # Na ordem: parágrafo, linha (§ e incisos), frase, palavra.
    separators=["\n\n", "\n", "; ", ". ", " "],
)


@dataclass
class Documento:
    arquivo: str
    titulo: str
    fonte: str
    url: str
    tipo: str
    corpo: str


@dataclass
class Trecho:
    id: str
    fonte: str
    titulo_documento: str
    referencia: str  # "Art. 49" ou o nome da seção
    caminho: str  # cabeçalho de contexto
    texto: str
    url: str
    tipo: str
    tokens: int = field(default=0)

    @property
    def texto_indexado(self) -> str:
        """O que vira embedding e entra no BM25: cabeçalho + texto."""
        return f"{self.caminho}\n{self.texto}"


def _ler(caminho: Path) -> Documento:
    bruto = caminho.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    corpo = bruto
    if bruto.startswith("---"):
        _, cabecalho, corpo = bruto.split("---", 2)
        for linha in cabecalho.strip().splitlines():
            chave, _, valor = linha.partition(":")
            meta[chave.strip()] = valor.strip()
    return Documento(
        arquivo=caminho.name,
        titulo=meta.get("titulo", caminho.stem),
        fonte=meta.get("fonte", caminho.stem),
        url=meta.get("url", ""),
        tipo=meta.get("tipo", "empresa"),
        corpo=corpo.strip(),
    )


def carregar_documentos(pasta: Path = PASTA_DOCUMENTOS) -> list[Documento]:
    return [_ler(caminho) for caminho in sorted(pasta.glob("*.md"))]


def _id(fonte: str, referencia: str, texto: str) -> str:
    # O id depende do CONTEÚDO: se o texto não mudou, o id é o mesmo e a
    # reindexação não paga o embedding de novo (ver app.rag.indice).
    return hashlib.sha256(f"{fonte}|{referencia}|{texto}".encode()).hexdigest()[:24]


def _nome_estrutura(linha: str) -> str:
    """'## CAPÍTULO VI Da Proteção Contratual' -> 'Cap. VI — Da Proteção Contratual'."""
    texto = linha.lstrip("#").strip()
    texto = re.sub(r"^CAP[ÍI]TULO", "Cap.", texto, flags=re.IGNORECASE)
    texto = re.sub(r"^SE[ÇC][ÃA]O", "Seção", texto, flags=re.IGNORECASE)
    texto = re.sub(r"^T[ÍI]TULO", "Título", texto, flags=re.IGNORECASE)
    return re.sub(r"^((?:Cap\.|Seção|Título)\s+[IVXLC]+(?:-[A-Z])?)\s+(?!—)", r"\1 — ", texto)


def _blocos_da_lei(documento: Documento) -> list[tuple[str, str, str]]:
    """(referência, caminho, texto) por artigo."""
    blocos: list[tuple[str, str, str]] = []
    capitulo = secao = ""
    atual: list[str] = []
    referencia = ""

    def fechar() -> None:
        # "Este Decreto entra em vigor..." e "Revoga-se..." não respondem cliente
        # nenhum: só competiriam na busca por palavras como "decreto" e "prazo".
        if atual and len(atual) == 1 and SEM_CONTEUDO.search(atual[0]):
            return
        if atual and referencia:
            partes = [documento.fonte, capitulo, secao, referencia]
            blocos.append((referencia, " > ".join(p for p in partes if p), "\n".join(atual)))

    for linha in documento.corpo.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("# "):
            continue
        if linha.startswith("## "):
            fechar(); atual, referencia = [], ""
            nome = _nome_estrutura(linha)
            if not nome.startswith("Título"):
                capitulo, secao = nome, ""
            continue
        if linha.startswith("### "):
            fechar(); atual, referencia = [], ""
            secao = _nome_estrutura(linha)
            continue
        artigo = ARTIGO.match(linha)
        if artigo:
            fechar()
            atual, referencia = [linha], f"Art. {artigo.group(1)}"
        elif referencia:
            atual.append(linha)
    fechar()
    return blocos


def _blocos_do_manual(documento: Documento) -> list[tuple[str, str, str]]:
    """(referência, caminho, texto) por seção '##'."""
    blocos: list[tuple[str, str, str]] = []
    for parte in re.split(r"^## ", documento.corpo, flags=re.MULTILINE)[1:]:
        titulo, _, texto = parte.partition("\n")
        titulo, texto = titulo.strip(), texto.strip()
        if texto:
            blocos.append((titulo, f"{documento.fonte} > {titulo}", texto))
    return blocos


def cortar(documento: Documento) -> list[Trecho]:
    blocos = _blocos_da_lei(documento) if documento.tipo == "lei" else _blocos_do_manual(documento)
    trechos: list[Trecho] = []
    for referencia, caminho, texto in blocos:
        pedacos = [texto] if contar_tokens(texto) <= MAXIMO_TOKENS else _divisor.split_text(texto)
        for indice, pedaco in enumerate(pedacos, start=1):
            ref = referencia if len(pedacos) == 1 else f"{referencia} (parte {indice}/{len(pedacos)})"
            trecho = Trecho(
                id=_id(documento.fonte, ref, pedaco),
                fonte=documento.fonte,
                titulo_documento=documento.titulo,
                referencia=ref,
                caminho=caminho if len(pedacos) == 1 else f"{caminho} (parte {indice}/{len(pedacos)})",
                texto=pedaco,
                url=documento.url,
                tipo=documento.tipo,
            )
            trecho.tokens = contar_tokens(trecho.texto_indexado)
            trechos.append(trecho)
    return trechos


def todos_os_trechos() -> list[Trecho]:
    return [trecho for documento in carregar_documentos() for trecho in cortar(documento)]
