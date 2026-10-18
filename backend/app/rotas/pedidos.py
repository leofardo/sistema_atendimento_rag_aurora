"""Pedidos fictícios da Aurora (os que o atendimento consulta)."""

from fastapi import APIRouter, HTTPException, status

from app.esquemas import ListaDePedidos, Pedido
from app.repositorios import pedidos

rota = APIRouter(prefix="/pedidos", tags=["Pedidos"])


@rota.get("", response_model=ListaDePedidos, summary="Listar pedidos")
def listar_pedidos() -> dict:
    return {"pedidos": pedidos.listar()}


@rota.get("/{numero}", response_model=Pedido, summary="Consultar pedido",
          responses={404: {"description": "Pedido não encontrado."}})
def consultar_pedido(numero: str) -> dict:
    pedido = pedidos.buscar(numero)
    if not pedido:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pedido não encontrado.")
    return pedido
