"""Chamadas à OpenAI via HTTP, sem SDK — o mesmo caminho do Financeiro Pessoal.

Três formatos de chamada, cada um devolvendo o `usage` que a API reportou,
porque é ele que vira custo no livro-caixa:

- embeddings(): vetores de uma lista de textos (em lotes).
- chat_json(): resposta estruturada por JSON Schema (o roteador).
- chat_stream(): resposta em streaming para o cliente.
"""

import hashlib
import json
import os
from typing import Any, AsyncIterator

import httpx

from app.nucleo.configuracao import dimensoes_embedding, modelo_embedding, numero_ambiente

URL_BASE = "https://api.openai.com/v1"
TAMANHO_LOTE_EMBEDDING = 128


class ErroOpenAI(RuntimeError):
    pass


def _chave() -> str:
    chave = os.getenv("OPENAI_API_KEY", "").strip()
    if not chave or chave.startswith("coloque"):
        raise ErroOpenAI("OPENAI_API_KEY não configurada em backend/.env.")
    return chave


def _cabecalhos() -> dict[str, str]:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {_chave()}"}


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        numero_ambiente("OPENAI_TIMEOUT_SECONDS", 60.0),
        connect=numero_ambiente("OPENAI_CONNECT_TIMEOUT_SECONDS", 10.0),
    )


_cliente: httpx.AsyncClient | None = None


def _cliente_http() -> httpx.AsyncClient:
    """Um cliente só, com a conexão mantida aberta (keep-alive).

    Abrir um cliente por chamada refazia DNS + TLS a cada vez: o embedding de
    11 tokens levava 1,7 s, quase tudo handshake. Reaproveitando, cai para ~0,3 s.
    """
    global _cliente
    if _cliente is None or _cliente.is_closed:
        _cliente = httpx.AsyncClient(
            base_url=URL_BASE, timeout=_timeout(),
            limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=120),
        )
    return _cliente


async def fechar() -> None:
    if _cliente is not None and not _cliente.is_closed:
        await _cliente.aclose()


def _mensagem_de_erro(resposta: httpx.Response) -> str:
    try:
        erro = resposta.json().get("error") or {}
        return str(erro.get("message") or f"Erro {resposta.status_code} na OpenAI.")
    except ValueError:
        return f"Erro {resposta.status_code} na OpenAI."


async def _post(caminho: str, corpo: dict[str, Any]) -> dict[str, Any]:
    try:
        resposta = await _cliente_http().post(caminho, headers=_cabecalhos(), json=corpo)
    except httpx.TimeoutException as erro:
        raise ErroOpenAI("Tempo limite ao consultar a OpenAI.") from erro
    except httpx.HTTPError as erro:
        raise ErroOpenAI("Não foi possível conectar com a OpenAI.") from erro
    if resposta.status_code >= 400:
        raise ErroOpenAI(_mensagem_de_erro(resposta))
    return resposta.json()


async def embeddings(textos: list[str], modelo: str | None = None) -> tuple[list[list[float]], int]:
    """Vetores na ordem dos textos e o total de tokens cobrados.

    Em lote: uma requisição com 128 textos custa o mesmo que 128 requisições
    (o preço é por token), mas é muito mais rápida e não esbarra no limite de
    requisições por minuto.
    """
    modelo = modelo or modelo_embedding()
    vetores: list[list[float]] = []
    tokens = 0
    for inicio in range(0, len(textos), TAMANHO_LOTE_EMBEDDING):
        lote = textos[inicio:inicio + TAMANHO_LOTE_EMBEDDING]
        corpo: dict[str, Any] = {"model": modelo, "input": lote}
        if modelo.startswith("text-embedding-3"):
            corpo["dimensions"] = dimensoes_embedding()
        dados = await _post("/embeddings", corpo)
        vetores += [item["embedding"] for item in sorted(dados["data"], key=lambda item: item["index"])]
        tokens += (dados.get("usage") or {}).get("prompt_tokens", 0)
    return vetores, tokens


def _chave_de_cache(mensagens: list[dict[str, Any]], modelo: str) -> str:
    """`prompt_cache_key`: manda chamadas com o MESMO prefixo para o mesmo servidor de cache.

    Sem isso, com pouco tráfego, a requisição cai em máquinas diferentes e o
    desconto de 75% do prefixo repetido falha de forma aleatória (o teste real
    mostrou a resposta sem cache nenhum numa conversa inteira).
    """
    sistema = str(mensagens[0].get("content") or "") if mensagens and mensagens[0].get("role") == "system" else ""
    return f"aurora-{modelo}-{hashlib.sha256(sistema.encode()).hexdigest()[:12]}"


async def chat_json(
    mensagens: list[dict[str, Any]], modelo: str, esquema: dict[str, Any], max_tokens: int = 200
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resposta que SEMPRE obedece o JSON Schema (structured outputs).

    Com `strict`, a API garante o formato: nada de "```json", campo faltando
    ou texto antes do JSON — o que, num roteador, viraria retentativa paga.
    """
    dados = await _post(
        "/chat/completions",
        {
            "model": modelo,
            "messages": mensagens,
            "temperature": 0,
            "max_completion_tokens": max_tokens,
            "prompt_cache_key": _chave_de_cache(mensagens, modelo),
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "saida", "strict": True, "schema": esquema},
            },
        },
    )
    conteudo = dados["choices"][0]["message"].get("content") or "{}"
    try:
        return json.loads(conteudo), dados.get("usage") or {}
    except ValueError as erro:
        raise ErroOpenAI("O roteador devolveu um JSON inválido.") from erro


async def chat_texto(mensagens: list[dict[str, Any]], modelo: str, max_tokens: int = 300) -> tuple[str, dict[str, Any]]:
    dados = await _post(
        "/chat/completions",
        {"model": modelo, "messages": mensagens, "temperature": 0.2, "max_completion_tokens": max_tokens},
    )
    return str(dados["choices"][0]["message"].get("content") or "").strip(), dados.get("usage") or {}


async def chat_stream(
    mensagens: list[dict[str, Any]], modelo: str, max_tokens: int, estado: dict[str, Any]
) -> AsyncIterator[str]:
    """Repassa o texto conforme chega; o `usage` final fica em estado["uso"]."""
    corpo = {
        "model": modelo,
        "messages": mensagens,
        "temperature": 0.2,
        "max_completion_tokens": max_tokens,
        "prompt_cache_key": _chave_de_cache(mensagens, modelo),
        "stream": True,
        # Sem isto, o streaming não reporta uso e o custo viraria estimativa.
        "stream_options": {"include_usage": True},
    }
    try:
        async with _cliente_http().stream("POST", "/chat/completions", headers=_cabecalhos(), json=corpo) as resposta:
            if resposta.status_code >= 400:
                await resposta.aread()
                raise ErroOpenAI(_mensagem_de_erro(resposta))
            async for linha in resposta.aiter_lines():
                if not linha.startswith("data:"):
                    continue
                pedaco = linha[5:].strip()
                if pedaco == "[DONE]":
                    break
                try:
                    evento = json.loads(pedaco)
                except ValueError:
                    continue
                if evento.get("usage"):
                    estado["uso"] = evento["usage"]
                escolhas = evento.get("choices") or []
                texto = (escolhas[0].get("delta") or {}).get("content") if escolhas else None
                if texto:
                    yield texto
    except httpx.TimeoutException as erro:
        raise ErroOpenAI("Tempo limite ao consultar a OpenAI.") from erro
    except httpx.HTTPError as erro:
        raise ErroOpenAI("Não foi possível conectar com a OpenAI.") from erro
