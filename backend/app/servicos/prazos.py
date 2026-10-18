"""Prazos calculados pelo CÓDIGO, não pelo modelo.

"Comprei dia 3, ainda dá para devolver?" exige contar dias — o tipo de conta
que o modelo erra e que, quando acerta, gasta tokens de raciocínio. Aqui a
conta é exata e vai pronta para o prompt numa linha: o modelo só comunica.

As regras espelham o manual da Aurora e o CDC:
- Arrependimento (compra fora da loja): 7 dias corridos do recebimento (art. 49).
- Troca por cortesia (compra em loja física): 30 dias corridos da compra.
- Defeito em até 7 dias do recebimento: troca direta, sem assistência.
- Garantia legal: 30 dias (não durável) ou 90 dias (durável) da entrega (art. 26).
"""

from datetime import date, timedelta

DIAS_ARREPENDIMENTO = 7
DIAS_CORTESIA_LOJA = 30
DIAS_TROCA_DIRETA_DEFEITO = 7
DIAS_GARANTIA_DURAVEL = 90
DIAS_GARANTIA_NAO_DURAVEL = 30


def _situacao(limite: date, hoje: date) -> str:
    dias = (limite - hoje).days
    if dias < 0:
        return f"até {limite.strftime('%d/%m/%Y')} — VENCIDO há {-dias} dia(s)"
    if dias == 0:
        return f"até {limite.strftime('%d/%m/%Y')} — vence HOJE"
    return f"até {limite.strftime('%d/%m/%Y')} — faltam {dias} dia(s)"


def calcular_prazos(
    referencia: date,
    hoje: date,
    canal: str | None = None,
    duravel: bool | None = True,
    data_compra: date | None = None,
) -> list[str]:
    """Linhas curtas com cada prazo aplicável. `referencia` é o dia do recebimento."""
    linhas = []
    if canal != "loja":
        linhas.append(f"prazo_arrependimento (CDC art. 49, 7 dias do recebimento): "
                      f"{_situacao(referencia + timedelta(days=DIAS_ARREPENDIMENTO), hoje)}")
    if canal == "loja":
        compra = data_compra or referencia
        linhas.append(f"troca_por_cortesia_loja (30 dias da compra): "
                      f"{_situacao(compra + timedelta(days=DIAS_CORTESIA_LOJA), hoje)}")
    linhas.append(f"troca_direta_por_defeito (7 dias do recebimento): "
                  f"{_situacao(referencia + timedelta(days=DIAS_TROCA_DIRETA_DEFEITO), hoje)}")
    dias_garantia = DIAS_GARANTIA_NAO_DURAVEL if duravel is False else DIAS_GARANTIA_DURAVEL
    tipo = "não durável" if duravel is False else "durável"
    linhas.append(f"garantia_legal ({tipo}, {dias_garantia} dias da entrega, CDC art. 26): "
                  f"{_situacao(referencia + timedelta(days=dias_garantia), hoje)}")
    return linhas
