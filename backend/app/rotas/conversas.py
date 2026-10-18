"""Conversas de atendimento: criar, listar, abrir, apagar e enviar mensagens."""

import json
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.esquemas import ConversaCriada, ConversaDetalhe, ListaDeConversas, NovaMensagem
from app.repositorios import custos
from app.repositorios.memoria import obter_memoria
from app.servicos.atendente import atender, resumir_se_preciso

rota = APIRouter(prefix="/conversas", tags=["Conversas"])


def _exigir_conversa(conversa_id: str) -> None:
    if not obter_memoria().existe(conversa_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada.")


@rota.post("", status_code=status.HTTP_201_CREATED, response_model=ConversaCriada, summary="Criar conversa")
def criar_conversa() -> dict:
    """Abre uma conversa nova e gera o número de protocolo do atendimento."""
    conversa_id = uuid.uuid4().hex
    return {"id": conversa_id, "protocolo": obter_memoria().protocolo(conversa_id)}


@rota.get("", response_model=ListaDeConversas, summary="Listar conversas")
def listar_conversas(limite: int = 30) -> dict:
    """Conversas com pelo menos uma mensagem, da mais recente para a mais antiga, com o custo de cada uma."""
    conversas = obter_memoria().listar(min(limite, 100))
    gasto = custos.custo_por_sessao([c["sessao"] for c in conversas])
    return {"conversas": [
        {"id": c["sessao"], "protocolo": c["protocolo"], "titulo": c["titulo"], "atualizadaEm": c["atualizada_em"],
         "mensagens": c["mensagens"], "brl": gasto.get(c["sessao"], 0.0)}
        for c in conversas
    ]}


@rota.get("/{conversa_id}", response_model=ConversaDetalhe, summary="Abrir conversa",
          responses={404: {"description": "Conversa não encontrada."}})
def abrir_conversa(conversa_id: str) -> dict:
    """A conversa inteira, com custos, rota e fontes de cada resposta."""
    _exigir_conversa(conversa_id)
    memoria = obter_memoria()
    resumo, _ = memoria.resumo(conversa_id)
    mensagens = memoria.conversa_completa(conversa_id)
    respostas = [m for m in mensagens if m["autor"] == "assistente"]
    if any(not m.get("meta") for m in respostas):
        # Respostas antigas, sem metadados: reconstrói pela ordem dos turnos no livro-caixa.
        metas = custos.metas_dos_turnos(conversa_id)
        if len(metas) == len(respostas):
            for resposta, meta in zip(respostas, metas):
                resposta["meta"] = resposta.get("meta") or meta
    return {"id": conversa_id, "protocolo": memoria.protocolo(conversa_id), "resumo": resumo,
            "mensagens": mensagens, "memoria": memoria.nome()}


@rota.delete("/{conversa_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Apagar conversa",
             responses={404: {"description": "Conversa não encontrada."}})
def apagar_conversa(conversa_id: str) -> Response:
    """Apaga as mensagens e o resumo. Os custos continuam no livro-caixa: o dinheiro foi gasto."""
    _exigir_conversa(conversa_id)
    obter_memoria().apagar(conversa_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@rota.post(
    "/{conversa_id}/mensagens", summary="Enviar mensagem",
    response_class=StreamingResponse,
    responses={
        200: {"description": "Resposta em streaming, um evento JSON por linha (NDJSON): `inicio`, vários "
                             "`delta` com pedaços do texto e `fim` com rota, intenção, custos e fontes.",
              "content": {"application/x-ndjson": {}}},
        404: {"description": "Conversa não encontrada."},
    },
)
async def enviar_mensagem(conversa_id: str, mensagem: NovaMensagem) -> StreamingResponse:
    """Envia a mensagem do cliente e devolve a resposta da Lia em streaming."""
    _exigir_conversa(conversa_id)

    async def eventos() -> AsyncIterator[str]:
        async for evento in atender(conversa_id, mensagem.texto.strip()):
            yield json.dumps(evento, ensure_ascii=False) + "\n"

    return StreamingResponse(
        eventos(),
        media_type="application/x-ndjson",
        # O resumo da conversa roda depois que a resposta terminou de sair.
        background=BackgroundTask(resumir_se_preciso, conversa_id),
    )
