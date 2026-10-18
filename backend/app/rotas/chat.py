"""Chat em streaming (NDJSON: um evento JSON por linha)."""

import json
import uuid
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.repositorios import custos
from app.repositorios.memoria import obter_memoria
from app.servicos.atendente import atender, resumir_se_preciso

rota = APIRouter(prefix="/api/chat", tags=["chat"])


class Pergunta(BaseModel):
    sessao: str = Field(min_length=8, max_length=64)
    mensagem: str = Field(min_length=1, max_length=2000)


@rota.post("/sessao")
def nova_sessao() -> dict[str, str]:
    sessao = uuid.uuid4().hex
    return {"sessao": sessao, "protocolo": obter_memoria().protocolo(sessao)}


@rota.get("/sessao/{sessao}")
def obter_sessao(sessao: str) -> dict:
    """A conversa inteira, com custos, rota e fontes de cada resposta (para reabrir do histórico)."""
    memoria = obter_memoria()
    resumo, _ = memoria.resumo(sessao)
    mensagens = memoria.conversa_completa(sessao)
    respostas = [m for m in mensagens if m["autor"] == "assistente"]
    if any(not m.get("meta") for m in respostas):
        # Respostas antigas, sem metadados: reconstrói pela ordem dos turnos no livro-caixa.
        metas = custos.metas_dos_turnos(sessao)
        if len(metas) == len(respostas):
            for resposta, meta in zip(respostas, metas):
                resposta["meta"] = resposta.get("meta") or meta
    return {"sessao": sessao, "protocolo": memoria.protocolo(sessao), "resumo": resumo,
            "mensagens": mensagens, "memoria": memoria.nome()}


@rota.delete("/sessao/{sessao}")
def apagar_sessao(sessao: str) -> dict[str, bool]:
    """Apaga a conversa (mensagens e resumo). Os custos continuam no livro-caixa."""
    obter_memoria().apagar(sessao)
    return {"ok": True}


@rota.get("/conversas")
def listar_conversas(limite: int = 30) -> dict:
    """Conversas com pelo menos uma mensagem, da mais recente para a mais antiga."""
    conversas = obter_memoria().listar(min(limite, 100))
    gasto = custos.custo_por_sessao([c["sessao"] for c in conversas])
    return {"conversas": [{**c, "brl": gasto.get(c["sessao"], 0.0)} for c in conversas]}


@rota.post("/stream")
async def conversar(pergunta: Pergunta) -> StreamingResponse:
    async def eventos() -> AsyncIterator[str]:
        async for evento in atender(pergunta.sessao, pergunta.mensagem.strip()):
            yield json.dumps(evento, ensure_ascii=False) + "\n"

    return StreamingResponse(
        eventos(),
        media_type="application/x-ndjson",
        # O resumo da conversa roda depois que a resposta terminou de sair.
        background=BackgroundTask(resumir_se_preciso, pergunta.sessao),
    )
