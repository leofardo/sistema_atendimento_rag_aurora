"""API do atendimento da Aurora Casa & Tech."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.nucleo.banco import iniciar_banco  # noqa: E402
from app.nucleo.configuracao import modelo_embedding, origens_permitidas  # noqa: E402
from app.nucleo.precos import calcular  # noqa: E402
from app.rag import cache_semantico  # noqa: E402
from app.rag.indice import obter_indice  # noqa: E402
from app.repositorios import custos  # noqa: E402
from app.repositorios.memoria import obter_memoria  # noqa: E402
from app.rotas import base, chat  # noqa: E402
from app.rotas import custos as rotas_custos  # noqa: E402
from app.servicos.cotacao import atualizar_cotacao  # noqa: E402
from app.servicos.openai_cliente import ErroOpenAI  # noqa: E402
from app.servicos.openai_cliente import fechar as fechar_openai  # noqa: E402

logger = logging.getLogger("uvicorn.error")


async def _sincronizar_base() -> None:
    """Reindexa ao subir: documento editado passa a valer sem ninguém lembrar de reindexar.

    Incremental: sem mudança, custa zero. Com mudança, paga só os trechos
    alterados e limpa o cache semântico (resposta velha não sobrevive à política nova).
    """
    try:
        resultado = await obter_indice().indexar()
    except ErroOpenAI as erro:
        logger.warning("Base não sincronizada ao iniciar: %s", erro)
        return
    if resultado["tokensEmbedding"]:
        custos.registrar("indexacao", calcular(modelo_embedding(), resultado["tokensEmbedding"]), resultado["duracaoMs"])
    if resultado["novos"] or resultado["removidos"]:
        cache_semantico.limpar()
        logger.warning("Base atualizada ao iniciar: %s novos, %s removidos; cache semântico limpo.",
                       resultado["novos"], resultado["removidos"])


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    iniciar_banco()
    obter_memoria()  # com REDIS_URL, falha aqui se o Redis não responder
    await atualizar_cotacao()
    await _sincronizar_base()
    yield
    await fechar_openai()


app = FastAPI(title="API Atendimento Aurora", lifespan=ciclo_de_vida)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origens_permitidas(),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat.rota)
app.include_router(rotas_custos.rota)
app.include_router(base.rota)


@app.get("/health")
def saude() -> dict:
    return {"ok": True, "trechosIndexados": obter_indice().total(), "memoria": obter_memoria().nome()}
