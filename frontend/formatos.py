"""Formatação brasileira de dinheiro e números."""

import html
from datetime import datetime, timedelta, timezone

# O banco grava em UTC; a tela mostra no horário de Brasília (UTC-3, sem horário de verão desde 2019).
BRASILIA = timezone(timedelta(hours=-3))


def data_hora(utc: str | None) -> str:
    """'2026-10-18 00:00:41' (UTC) -> '17/10/2026 21:00:41' (Brasília)."""
    if not utc:
        return ""
    try:
        instante = datetime.strptime(utc[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return utc
    return instante.astimezone(BRASILIA).strftime("%d/%m/%Y %H:%M:%S")


def _br(numero: str) -> str:
    return numero.replace(",", "_").replace(".", ",").replace("_", ".")


def brl(valor: float, casas: int | None = None) -> str:
    """Custo de IA é fração de centavo: 4 casas por padrão, 2 a partir de R$ 1."""
    casas = casas if casas is not None else (2 if abs(valor) >= 1 else 4)
    return f"R$ {_br(f'{valor:,.{casas}f}')}"


def usd(valor: float) -> str:
    return f"US$ {valor:,.5f}" if abs(valor) < 1 else f"US$ {valor:,.2f}"


def inteiro(valor: float) -> str:
    return _br(f"{int(valor):,}")


def porcentagem(parte: float, total: float) -> str:
    return "0%" if not total else f"{100 * parte / total:.0f}%".replace(".", ",")


def md(texto: str) -> str:
    """Escapa o $ para Markdown: entre dois $, o Streamlit desenha fórmula (LaTeX).

    Era o bug do chat: "R$ 0,0002 (US$ 0.00005)" virava "R 0,0002 (US 0.00005)".
    """
    return texto.replace("$", "\\$")


def h(texto: object) -> str:
    """Escapa para HTML, incluindo o $ (que o Markdown leria como fórmula)."""
    return html.escape(str(texto)).replace("$", "&#36;")
