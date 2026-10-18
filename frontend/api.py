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
# Toda chamada vai para a v1 da API: os caminhos abaixo são só o recurso ("/conversas", "/pedidos"...).
_cliente = httpx.Client(base_url=f"{URL}/api/v1", timeout=httpx.Timeout(90.0, connect=5.0))
FORA_DO_AR = f"API fora do ar em {URL}. Suba o backend: uvicorn app.main:app (dentro de backend/)."


class ErroApi(RuntimeError):
    pass


def _cabecalhos() -> dict[str, str]:
    # A API exige a senha em todo endpoint; o frontend a manda sozinho, lida do
    # frontend/.env (API_SENHA), e o usuário do chat não precisa digitar nada.
    return {"X-Senha": os.getenv("API_SENHA", "")}


SENHA_ERRADA = "A API recusou a senha: confira se API_SENHA (frontend/.env) é igual a AUTH_SENHA (backend/.env)."


def _tratar(resposta: httpx.Response) -> Any:
    if resposta.status_code == 401:
        raise ErroApi(SENHA_ERRADA)
    if resposta.status_code >= 400:
        try:
            detalhe = resposta.json().get("detail") or resposta.text
        except ValueError:
            detalhe = resposta.text
        raise ErroApi(str(detalhe))
    if resposta.status_code == 204 or not resposta.content:
        return None  # DELETE devolve 204, sem corpo
    return resposta.json()


def get(caminho: str, **params: Any) -> Any:
    try:
        return _tratar(_cliente.get(caminho, params={k: v for k, v in params.items() if v is not None},
                                    headers=_cabecalhos()))
    except httpx.HTTPError as erro:
        raise ErroApi(FORA_DO_AR) from erro


def post(caminho: str, corpo: dict | None = None) -> Any:
    try:
        return _tratar(_cliente.post(caminho, json=corpo or {}, headers=_cabecalhos()))
    except httpx.HTTPError as erro:
        raise ErroApi(FORA_DO_AR) from erro


def delete(caminho: str) -> Any:
    try:
        return _tratar(_cliente.delete(caminho, headers=_cabecalhos()))
    except httpx.HTTPError as erro:
        raise ErroApi(FORA_DO_AR) from erro


def conversar(sessao: str, mensagem: str) -> Iterator[dict[str, Any]]:
    """Eventos NDJSON do chat: inicio, delta (texto), erro, fim (custos, rota, fontes)."""
    try:
        with _cliente.stream("POST", f"/conversas/{sessao}/mensagens", json={"texto": mensagem},
                             headers=_cabecalhos()) as resposta:
            if resposta.status_code == 401:
                yield {"tipo": "erro", "mensagem": SENHA_ERRADA}
                return
            if resposta.status_code == 404:
                yield {"tipo": "erro", "mensagem": "Esta conversa não existe mais. Abra uma nova conversa."}
                return
            if resposta.status_code >= 400:
                resposta.read()
                yield {"tipo": "erro", "mensagem": resposta.text}
                return
            for linha in resposta.iter_lines():
                if linha.strip():
                    yield json.loads(linha)
    except httpx.HTTPError:
        yield {"tipo": "erro", "mensagem": FORA_DO_AR}
