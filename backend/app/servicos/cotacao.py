"""Cotação do dólar — buscada uma vez por dia, com valor fixo de reserva.

O preço da OpenAI é em dólar; a tela mostra em real. Buscar a cotação a cada
tela deixaria o app dependente de mais uma API para exibir um número, então
aqui ela é buscada no máximo UMA vez por dia e fica em memória.

Duas fontes, em ordem: AwesomeAPI e, se ela falhar, a PTAX do Banco Central
(série SGS 1). A AwesomeAPI tem cota por IP e responde 429 na VPS; antes disso
o app caía direto no valor fixo e ficava nele. Se as duas falharem, vale o
COTACAO_DOLAR do .env — custo é informação, não dado crítico — e a próxima
tentativa só acontece depois de ESPERA_APOS_FALHA, para não bater na API a
cada mensagem do chat (o que, numa API com cota, só prolonga o bloqueio).
"""

import os
from datetime import date, datetime, timedelta

import httpx

URL_AWESOMEAPI = "https://economia.awesomeapi.com.br/last/USD-BRL"
URL_BANCO_CENTRAL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados/ultimos/1?formato=json"
ESPERA_APOS_FALHA = timedelta(hours=1)

_cotacao: float | None = None
_origem: str = "valor fixo"
_buscada_em: date | None = None
_proxima_tentativa: datetime | None = None


def _reserva() -> float:
    try:
        return float(os.getenv("COTACAO_DOLAR", 5.13))
    except (TypeError, ValueError):
        return 5.13


def cotacao_atual() -> float:
    """O valor em memória, sem ir à rede. É o que as contas usam."""
    return _cotacao if _cotacao is not None else _reserva()


def origem_da_cotacao() -> str:
    return _origem if _cotacao is not None else "valor fixo"


async def _da_awesomeapi(cliente: httpx.AsyncClient) -> float:
    resposta = await cliente.get(URL_AWESOMEAPI)
    resposta.raise_for_status()
    return float(resposta.json()["USDBRL"]["bid"])


async def _do_banco_central(cliente: httpx.AsyncClient) -> float:
    resposta = await cliente.get(URL_BANCO_CENTRAL)
    resposta.raise_for_status()
    return float(resposta.json()[-1]["valor"])


async def atualizar_cotacao() -> float:
    """Busca a cotação do dia, no máximo uma vez por dia."""
    global _cotacao, _origem, _buscada_em, _proxima_tentativa

    if _buscada_em == date.today():
        return cotacao_atual()
    if _proxima_tentativa and datetime.now() < _proxima_tentativa:
        return cotacao_atual()

    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0)) as cliente:
        for origem, buscar in (("AwesomeAPI", _da_awesomeapi), ("Banco Central", _do_banco_central)):
            try:
                valor = await buscar(cliente)
            except Exception:
                continue
            if valor > 0:
                _cotacao, _origem, _buscada_em, _proxima_tentativa = valor, origem, date.today(), None
                return valor

    # Sem cotação nova, segue com a última boa (ou a reserva) e espera para tentar de novo.
    _proxima_tentativa = datetime.now() + ESPERA_APOS_FALHA
    return cotacao_atual()
