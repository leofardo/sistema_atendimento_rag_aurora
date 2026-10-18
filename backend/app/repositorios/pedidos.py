"""Pedidos fictícios da Aurora.

Para o modelo vai só o que responde ao cliente, numa linha `campo=valor`:
status, itens, datas e rastreio. Nada de id interno, valor de auditoria ou
o objeto inteiro do banco (dica 4: formato compacto).
"""

from datetime import date
from typing import Any

from app.nucleo.banco import obter_conexao


def buscar(numero: str) -> dict[str, Any] | None:
    with obter_conexao() as conexao:
        linha = conexao.execute("SELECT * FROM pedidos WHERE numero = ?", (numero.strip(),)).fetchone()
    return dict(linha) if linha else None


def listar() -> list[dict[str, Any]]:
    with obter_conexao() as conexao:
        return [dict(l) for l in conexao.execute("SELECT * FROM pedidos ORDER BY numero").fetchall()]


def linha_compacta(pedido: dict[str, Any]) -> str:
    campos = [
        ("pedido", pedido["numero"]), ("status", pedido["status"]), ("itens", pedido["itens"]),
        ("canal", pedido["canal"]), ("pagamento", pedido["pagamento"]), ("valor", f"{pedido['valor']:.2f}"),
        ("compra", pedido["data_compra"]), ("previsao", pedido["previsao_entrega"]),
        ("entregue_em", pedido["data_entrega"]), ("rastreio", pedido["rastreio"]),
    ]
    return "; ".join(f"{nome}={valor}" for nome, valor in campos if valor)


def data(valor: str | None) -> date | None:
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None
