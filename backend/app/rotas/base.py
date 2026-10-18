"""Base de conhecimento: documentos, trechos, reindexação e busca de teste."""

import time
from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException

from app.nucleo.configuracao import modelo_embedding
from app.nucleo.precos import calcular
from app.rag import cache_semantico
from app.rag.documentos import carregar_documentos
from app.rag.indice import obter_indice
from app.repositorios import custos, pedidos
from app.servicos import openai_cliente
from app.servicos.atendente import _fontes
from app.servicos.cotacao import atualizar_cotacao

rota = APIRouter(prefix="/api/base", tags=["base de conhecimento"])


@rota.get("/documentos")
def listar_documentos() -> dict[str, Any]:
    trechos = obter_indice().listar_trechos()
    por_fonte = Counter(t["fonte"] for t in trechos)
    tokens = Counter()
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


@rota.get("/trechos")
def listar_trechos(fonte: str | None = None) -> dict[str, Any]:
    trechos = obter_indice().listar_trechos()
    if fonte:
        trechos = [t for t in trechos if t["fonte"] == fonte]
    return {"trechos": sorted(trechos, key=lambda t: (t["fonte"], t["caminho"]))}


@rota.post("/reindexar")
async def reindexar() -> dict[str, Any]:
    await atualizar_cotacao()
    try:
        resultado = await obter_indice().indexar()
    except openai_cliente.ErroOpenAI as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    custo = None
    if resultado["tokensEmbedding"]:
        custo = custos.registrar("indexacao", calcular(modelo_embedding(), resultado["tokensEmbedding"]),
                                 resultado["duracaoMs"])
    if resultado["novos"] or resultado["removidos"]:
        resultado["cacheLimpo"] = cache_semantico.limpar()
    return {**resultado, "custo": custo}


@rota.post("/cache/limpar")
def limpar_cache() -> dict[str, int]:
    return {"removidos": cache_semantico.limpar()}


@rota.get("/cache")
def listar_cache() -> dict[str, Any]:
    return {"itens": cache_semantico.listar()}


@rota.delete("/cache/{item_id}")
def apagar_do_cache(item_id: str) -> dict[str, bool]:
    cache_semantico.apagar(item_id)
    return {"ok": True}


@rota.get("/buscar")
async def buscar(q: str, k: int = 4) -> dict[str, Any]:
    """Mostra o que a busca híbrida devolve para uma consulta (custa só o embedding)."""
    await atualizar_cotacao()
    inicio = time.monotonic()
    try:
        vetores, tokens = await openai_cliente.embeddings([q])
    except openai_cliente.ErroOpenAI as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    custo = custos.registrar("embedding", calcular(modelo_embedding(), tokens), int((time.monotonic() - inicio) * 1000))
    return {"consulta": q, "resultados": _fontes(obter_indice().buscar(q, vetores[0], k=k)), "custo": custo}


@rota.get("/pedidos")
def listar_pedidos() -> dict[str, Any]:
    return {"pedidos": pedidos.listar()}
