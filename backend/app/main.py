"""API do atendimento da Aurora Casa & Tech."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.nucleo.banco import iniciar_banco  # noqa: E402
from app.nucleo.configuracao import modelo_embedding, origens_permitidas  # noqa: E402
from app.nucleo.precos import calcular  # noqa: E402
from app.nucleo.seguranca import exigir_acesso  # noqa: E402
from app.rag import cache_semantico  # noqa: E402
from app.rag.indice import obter_indice  # noqa: E402
from app.repositorios import custos  # noqa: E402
from app.repositorios.memoria import obter_memoria  # noqa: E402
from app.rotas import base, conversas, pedidos  # noqa: E402
from app.rotas import custos as rotas_custos  # noqa: E402
from app.servicos.cotacao import atualizar_cotacao  # noqa: E402
from app.servicos.openai_cliente import ErroOpenAI  # noqa: E402
from app.servicos.openai_cliente import aquecer as aquecer_openai  # noqa: E402
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
    await aquecer_openai()
    yield
    await fechar_openai()


# Ordem e descrição dos grupos no /docs.
GRUPOS = [
    {"name": "Conversas", "description": "Atendimento: criar conversa, enviar mensagem (resposta em streaming), "
                                         "listar, abrir e apagar."},
    {"name": "Base de conhecimento", "description": "Documentos do RAG, busca híbrida, indexação e cache semântico."},
    {"name": "Pedidos", "description": "Pedidos fictícios que o atendimento consulta."},
    {"name": "Custos", "description": "Livro-caixa: tokens e custo de cada chamada à OpenAI, em dólar e real."},
    {"name": "Configuração", "description": "Modelos, preços e parâmetros em uso."},
    {"name": "Sistema", "description": "Saúde da API (rota pública)."},
]

# /docs fica aberto; quem exige credencial são os endpoints (botão "Authorize", cabeçalho X-Senha).
app = FastAPI(
    title="API Atendimento Aurora", version="1.0.0", lifespan=ciclo_de_vida, redoc_url=None,
    openapi_tags=GRUPOS,
    swagger_ui_parameters={"persistAuthorization": True},  # a senha sobrevive ao F5 do /docs
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origens_permitidas(),
    allow_methods=["*"],
    allow_headers=["*"],
)
# Tudo sob /api/v1: dá para mudar a API numa v2 sem quebrar quem usa a v1.
V1 = "/api/v1"
protegido = [Depends(exigir_acesso)]
for roteador_v1 in (conversas.rota, base.rota, pedidos.rota, rotas_custos.rota, rotas_custos.rota_configuracao):
    app.include_router(roteador_v1, prefix=V1, dependencies=protegido,
                       responses={401: {"description": "Senha ausente ou incorreta (botão Authorize)."}})


@app.get("/health", tags=["Sistema"], summary="Saúde da API")
def saude() -> dict:
    """Pública: diz se a API está no ar, quantos trechos estão indexados e onde fica a memória."""
    return {"ok": True, "trechosIndexados": obter_indice().total(), "memoria": obter_memoria().nome()}
