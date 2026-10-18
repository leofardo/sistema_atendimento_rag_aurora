"""Livro-caixa e configuração de custos."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.nucleo.configuracao import (
    dimensoes_embedding,
    max_tokens_resposta,
    mensagens_no_historico,
    modelo_embedding,
    modelo_resposta,
    modelo_roteador,
    similaridade_cache,
    similaridade_minima,
    trechos_por_resposta,
)
from app.nucleo.precos import PRECOS_FINE_TUNING, PRECOS_POR_MILHAO, calcular, contar_tokens, preco_do_modelo
from app.repositorios import custos
from app.repositorios.memoria import obter_memoria
from app.servicos import prompt_resposta, roteador
from app.servicos.cotacao import atualizar_cotacao, origem_da_cotacao

rota = APIRouter(prefix="/api/custos", tags=["custos"])


class TextoParaEstimar(BaseModel):
    texto: str
    modelo: str | None = None


@rota.get("/resumo")
async def obter_resumo(desde: str | None = None, ate: str | None = None) -> dict[str, Any]:
    await atualizar_cotacao()
    return {**custos.resumo(desde, ate), "origemCotacao": origem_da_cotacao()}


@rota.get("/chamadas")
def listar_chamadas(limite: int = 200, desde: str | None = None, ate: str | None = None) -> dict[str, Any]:
    return {"chamadas": custos.listar_chamadas(min(limite, 1000), desde, ate)}


@rota.get("/turnos")
def listar_turnos(limite: int = 200, desde: str | None = None, ate: str | None = None) -> dict[str, Any]:
    return {"turnos": custos.listar_turnos(min(limite, 1000), desde, ate)}


@rota.get("/configuracao")
async def configuracao() -> dict[str, Any]:
    """Modelos, preços e quanto os prompts fixos custam a cada envio."""
    await atualizar_cotacao()
    resposta, rot = modelo_resposta(), modelo_roteador()

    def prompt(nome: str, texto: str, modelo: str) -> dict[str, Any]:
        tokens = contar_tokens(texto, modelo)
        cheio = calcular(modelo, tokens)
        em_cache = calcular(modelo, tokens, cache=tokens)
        return {"nome": nome, "modelo": modelo, "tokens": tokens, "elegivelCache": tokens >= 1024,
                "brlCheio": cheio["brlEntrada"], "brlEmCache": em_cache["brlEntrada"]}

    return {
        "modelos": {"resposta": resposta, "roteador": rot, "embedding": modelo_embedding()},
        "precos": {nome: preco_do_modelo(nome) for nome in {resposta, rot, modelo_embedding()}},
        "tabela": PRECOS_POR_MILHAO,
        "tabelaFineTuning": PRECOS_FINE_TUNING,
        "rag": {"topK": trechos_por_resposta(), "similaridadeMinima": similaridade_minima(),
                "similaridadeCache": similaridade_cache(), "dimensoesEmbedding": dimensoes_embedding()},
        "conversa": {"mensagensNoHistorico": mensagens_no_historico(), "maxTokensResposta": max_tokens_resposta(),
                     "memoria": obter_memoria().nome()},
        "prompts": [
            prompt("Sistema da resposta", prompt_resposta.SISTEMA, resposta),
            prompt("Roteador (prompt completo)", roteador.PROMPT_COMPLETO, "gpt-4.1-nano"),
            prompt("Roteador afinado (prompt curto)", roteador.PROMPT_CURTO, "ft:gpt-4.1-nano-2025-04-14"),
        ],
        "origemCotacao": origem_da_cotacao(),
    }


@rota.post("/estimar")
async def estimar(corpo: TextoParaEstimar) -> dict[str, Any]:
    await atualizar_cotacao()
    modelo = corpo.modelo or modelo_resposta()
    tokens = contar_tokens(corpo.texto, modelo)
    custo = calcular(modelo, tokens)
    return {"modelo": modelo, "tokens": tokens, "usd": custo["usdEntrada"], "brl": custo["brlEntrada"]}
