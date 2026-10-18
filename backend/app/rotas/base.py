"""Base de conhecimento: documentos, trechos (e busca), indexação e cache semântico."""

import time
from collections import Counter

from fastapi import APIRouter, HTTPException, Response, status

from app.esquemas import CacheLimpo, ListaDeDocumentos, ListaDeTrechos, ListaDoCache, ResultadoDaIndexacao
from app.nucleo.configuracao import modelo_embedding
from app.nucleo.precos import calcular
from app.rag import cache_semantico
from app.rag.documentos import carregar_documentos
from app.rag.indice import obter_indice
from app.repositorios import custos
from app.servicos import openai_cliente
from app.servicos.atendente import _fontes
from app.servicos.cotacao import atualizar_cotacao

rota = APIRouter(prefix="/base", tags=["Base de conhecimento"])
FALHA_OPENAI = {502: {"description": "A OpenAI não respondeu (embedding)."}}


@rota.get("/documentos", response_model=ListaDeDocumentos, summary="Listar documentos")
def listar_documentos() -> dict:
    """Documentos da base, com quantos trechos e tokens cada um tem no índice."""
    trechos = obter_indice().listar_trechos()
    por_fonte = Counter(t["fonte"] for t in trechos)
    tokens: Counter = Counter()
    for t in trechos:
        tokens[t["fonte"]] += int(t.get("tokens", 0))
    return {
        "documentos": [
            {"titulo": d.titulo, "fonte": d.fonte, "tipo": d.tipo, "url": d.url, "arquivo": d.arquivo,
             "trechos": por_fonte.get(d.fonte, 0), "tokens": tokens.get(d.fonte, 0)}
            for d in carregar_documentos()
        ],
        "totalTrechos": len(trechos),
        "cacheRespostas": cache_semantico.total(),
    }


@rota.get("/trechos", response_model=ListaDeTrechos, summary="Listar ou buscar trechos", responses=FALHA_OPENAI)
async def listar_trechos(fonte: str | None = None, busca: str | None = None, limite: int = 4) -> dict:
    """Sem `busca`: os trechos indexados (opcionalmente de uma `fonte`).

    Com `busca`: o que a busca híbrida do atendimento devolve para a frase
    (vetorial + BM25 unidas por RRF), com similaridade e posição em cada busca.
    Custa só o embedding da frase.
    """
    if not busca:
        trechos = obter_indice().listar_trechos()
        if fonte:
            trechos = [t for t in trechos if t["fonte"] == fonte]
        return {"trechos": sorted(trechos, key=lambda t: (t["fonte"], t["caminho"]))}

    await atualizar_cotacao()
    inicio = time.monotonic()
    try:
        vetores, tokens = await openai_cliente.embeddings([busca])
    except openai_cliente.ErroOpenAI as erro:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(erro)) from erro
    custo = custos.registrar("embedding", calcular(modelo_embedding(), tokens), int((time.monotonic() - inicio) * 1000))
    return {"trechos": _fontes(obter_indice().buscar(busca, vetores[0], k=limite, texto_original=busca)), "custo": custo}


@rota.post("/indexacoes", response_model=ResultadoDaIndexacao, summary="Reindexar a base", responses=FALHA_OPENAI)
async def reindexar() -> dict:
    """Sincroniza o índice com os documentos. Só trecho novo ou alterado paga embedding.

    Se algo mudou, o cache semântico é apagado: resposta antiga não sobrevive à política nova.
    """
    await atualizar_cotacao()
    try:
        resultado = await obter_indice().indexar()
    except openai_cliente.ErroOpenAI as erro:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(erro)) from erro
    custo = None
    if resultado["tokensEmbedding"]:
        custo = custos.registrar("indexacao", calcular(modelo_embedding(), resultado["tokensEmbedding"]),
                                 resultado["duracaoMs"])
    if resultado["novos"] or resultado["removidos"]:
        resultado["cacheLimpo"] = cache_semantico.limpar()
    return {**resultado, "custo": custo}


@rota.get("/cache", response_model=ListaDoCache, summary="Listar respostas em cache")
def listar_cache() -> dict:
    """Respostas guardadas no cache semântico, da mais nova para a mais velha."""
    return {"itens": cache_semantico.listar()}


@rota.delete("/cache", response_model=CacheLimpo, summary="Limpar o cache")
def limpar_cache() -> dict:
    """Apaga todas as respostas do cache semântico."""
    return {"removidos": cache_semantico.limpar()}


@rota.delete("/cache/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Apagar resposta do cache",
             responses={404: {"description": "Resposta não encontrada no cache."}})
def apagar_do_cache(item_id: str) -> Response:
    if not cache_semantico.existe(item_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resposta não encontrada no cache.")
    cache_semantico.apagar(item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
