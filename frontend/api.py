"""Cliente HTTP da API (FastAPI). Toda conversa com o backend passa por aqui.

Duas escolhas de desempenho, medidas:
- 127.0.0.1 em vez de "localhost": no Windows, "localhost" tenta IPv6 primeiro,
  falha e só então cai no IPv4 — 2,3 s POR requisição. Era isso que deixava a
  troca de página lenta (a tela de custos faz 5 requisições).
- Um httpx.Client reaproveitado (conexão aberta): 3 requisições em 0,02 s.
"""

import json
import os
from typing import Any, Iterator

import httpx
from dotenv import load_dotenv

load_dotenv()

URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/").replace("://localhost", "://127.0.0.1")
_cliente = httpx.Client(base_url=URL, timeout=httpx.Timeout(90.0, connect=5.0))
FORA_DO_AR = f"API fora do ar em {URL}. Suba o backend: uvicorn app.main:app (dentro de backend/)."


class ErroApi(RuntimeError):
    pass


def _tratar(resposta: httpx.Response) -> Any:
    if resposta.status_code >= 400:
        try:
            detalhe = resposta.json().get("detail") or resposta.text
        except ValueError:
            detalhe = resposta.text
        raise ErroApi(str(detalhe))
    return resposta.json()


def get(caminho: str, **params: Any) -> Any:
    try:
        return _tratar(_cliente.get(caminho, params={k: v for k, v in params.items() if v is not None}))
    except httpx.HTTPError as erro:
        raise ErroApi(FORA_DO_AR) from erro


def post(caminho: str, corpo: dict | None = None) -> Any:
    try:
        return _tratar(_cliente.post(caminho, json=corpo or {}))
    except httpx.HTTPError as erro:
        raise ErroApi(FORA_DO_AR) from erro


def delete(caminho: str) -> Any:
    try:
        return _tratar(_cliente.delete(caminho))
    except httpx.HTTPError as erro:
        raise ErroApi(FORA_DO_AR) from erro


def conversar(sessao: str, mensagem: str) -> Iterator[dict[str, Any]]:
    """Eventos NDJSON do chat: inicio, delta (texto), erro, fim (custos, rota, fontes)."""
    try:
        with _cliente.stream("POST", "/api/chat/stream", json={"sessao": sessao, "mensagem": mensagem}) as resposta:
            if resposta.status_code >= 400:
                resposta.read()
                yield {"tipo": "erro", "mensagem": resposta.text}
                return
            for linha in resposta.iter_lines():
                if linha.strip():
                    yield json.loads(linha)
    except httpx.HTTPError:
        yield {"tipo": "erro", "mensagem": FORA_DO_AR}
