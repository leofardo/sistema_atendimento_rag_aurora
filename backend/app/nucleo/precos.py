"""Tokens, dólar e real — quanto cada chamada à OpenAI custou.

Mesmo mapa de preços do Financeiro Pessoal (entrada, entrada em cache e saída,
por milhão de tokens), com o que este projeto acrescenta:

- EMBEDDING: só tem entrada. Paga uma vez por trecho na indexação e uma vez
  por pergunta na busca (~20 tokens, praticamente de graça).
- FINE-TUNING: modelo "ft:..." custa mais por token que o base. A economia
  vem de ele precisar de um prompt bem menor (ver app.servicos.roteador).

O número de tokens vem da própria API (`usage`); em streaming, só com
`stream_options.include_usage`. Sem isso, cai no tiktoken.
"""

from typing import Any

# Preço por MILHÃO de tokens, em dólar (developers.openai.com/api/docs/pricing,
# conferido em out/2026). Quando o preço mudar, é só editar aqui.
PRECOS_POR_MILHAO = {
    "gpt-4.1": {"entrada": 2.00, "cache": 0.50, "saida": 8.00},
    "gpt-4.1-mini": {"entrada": 0.40, "cache": 0.10, "saida": 1.60},
    "gpt-4.1-nano": {"entrada": 0.10, "cache": 0.025, "saida": 0.40},
    "gpt-4o-mini": {"entrada": 0.15, "cache": 0.075, "saida": 0.60},
    "gpt-5-mini": {"entrada": 0.25, "cache": 0.025, "saida": 2.00},
    "gpt-5-nano": {"entrada": 0.05, "cache": 0.005, "saida": 0.40},
    "gpt-5.4-mini": {"entrada": 0.75, "cache": 0.075, "saida": 4.50},
    "gpt-5.4-nano": {"entrada": 0.20, "cache": 0.02, "saida": 1.25},
    "text-embedding-3-small": {"entrada": 0.02, "cache": 0.02, "saida": 0.0},
    "text-embedding-3-large": {"entrada": 0.13, "cache": 0.13, "saida": 0.0},
}

# Inferência de modelo afinado (fine-tuned) e custo do treino, por milhão.
PRECOS_FINE_TUNING = {
    "gpt-4.1-nano": {"treino": 1.50, "entrada": 0.20, "cache": 0.05, "saida": 0.80},
    "gpt-4.1-mini": {"treino": 5.00, "entrada": 0.80, "cache": 0.20, "saida": 3.20},
    "gpt-4o-mini": {"treino": 3.00, "entrada": 0.30, "cache": 0.15, "saida": 1.20},
}

ROTULOS_DE_OPERACAO = {
    "roteamento": "Roteamento",
    "embedding": "Embedding da pergunta",
    "resposta": "Resposta",
    "resumo": "Resumo da conversa",
    "indexacao": "Indexação da base",
    "avaliacao": "Avaliação",
    "dataset": "Dataset de fine-tuning",
}


def cotacao_do_dolar() -> float:
    from app.servicos.cotacao import cotacao_atual

    return cotacao_atual()


def preco_do_modelo(modelo: str) -> dict[str, float] | None:
    """Preço do modelo; "ft:gpt-4.1-nano-2025-04-14:org::id" usa a tabela de fine-tuning."""
    if modelo.startswith("ft:"):
        base = modelo.split(":")[1]
        for nome, preco in PRECOS_FINE_TUNING.items():
            if base.startswith(nome):
                return preco
        return None
    if modelo in PRECOS_POR_MILHAO:
        return PRECOS_POR_MILHAO[modelo]
    # Versões datadas ("gpt-4.1-mini-2025-04-14") custam o mesmo que o apelido.
    candidatos = [nome for nome in PRECOS_POR_MILHAO if modelo.startswith(nome + "-")]
    return PRECOS_POR_MILHAO[max(candidatos, key=len)] if candidatos else None


def contar_tokens(texto: str, modelo: str = "gpt-4.1-mini") -> int:
    """Conta com o tokenizador que a OpenAI usa para cobrar (o200k nos modelos 4.1/5)."""
    if not texto:
        return 0
    try:
        import tiktoken

        try:
            codificador = tiktoken.encoding_for_model(modelo)
        except KeyError:
            codificador = tiktoken.get_encoding("o200k_base")
        return len(codificador.encode(texto))
    except Exception:
        # ~3,8 caracteres por token é o que se mede em português.
        return max(1, int(len(texto) / 3.8))


def contar_tokens_das_mensagens(mensagens: list[dict[str, Any]], modelo: str) -> int:
    total = sum(contar_tokens(str(m.get("content") or ""), modelo) + 4 for m in mensagens)
    return total + 2


def calcular(modelo: str, entrada: int, saida: int = 0, cache: int = 0) -> dict[str, Any]:
    """Converte tokens em dólar e real, com entrada e saída separadas."""
    preco = preco_do_modelo(modelo)
    cotacao = cotacao_do_dolar()
    if preco is None:
        # Modelo desconhecido: mostra os tokens e zero, em vez de inventar preço.
        return {
            "modelo": modelo, "entrada": entrada, "saida": saida, "cache": cache,
            "usd": 0.0, "brl": 0.0, "usdEntrada": 0.0, "usdSaida": 0.0,
            "brlEntrada": 0.0, "brlSaida": 0.0, "cotacao": cotacao, "precoConhecido": False,
        }

    # Tokens em cache já vêm somados na entrada e são cobrados mais barato.
    entrada_cheia = max(entrada - cache, 0)
    usd_entrada = (entrada_cheia * preco["entrada"] + cache * preco["cache"]) / 1_000_000
    usd_saida = saida * preco["saida"] / 1_000_000
    return {
        "modelo": modelo, "entrada": entrada, "saida": saida, "cache": cache,
        "usd": usd_entrada + usd_saida, "brl": (usd_entrada + usd_saida) * cotacao,
        "usdEntrada": usd_entrada, "usdSaida": usd_saida,
        "brlEntrada": usd_entrada * cotacao, "brlSaida": usd_saida * cotacao,
        "cotacao": cotacao, "precoConhecido": True,
    }


def custo_de_treino(modelo_base: str, tokens_por_epoca: int, epocas: int) -> dict[str, float]:
    """Estimativa do custo de um job de fine-tuning: tokens do dataset x épocas x preço."""
    preco = next((p for nome, p in PRECOS_FINE_TUNING.items() if modelo_base.startswith(nome)), None)
    usd = (tokens_por_epoca * epocas * preco["treino"] / 1_000_000) if preco else 0.0
    return {"usd": usd, "brl": usd * cotacao_do_dolar()}
