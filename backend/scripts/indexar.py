"""Indexa (ou reindexa) a base de conhecimento.

Uso (de dentro de backend/):  python -m scripts.indexar

Incremental: só trecho novo ou alterado paga embedding. Rodar de novo sem
mudar documento custa zero.
"""

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.nucleo.banco import iniciar_banco  # noqa: E402
from app.nucleo.configuracao import modelo_embedding  # noqa: E402
from app.nucleo.precos import calcular  # noqa: E402
from app.rag import cache_semantico  # noqa: E402
from app.rag.indice import obter_indice  # noqa: E402
from app.repositorios import custos  # noqa: E402
from app.servicos.cotacao import atualizar_cotacao  # noqa: E402


async def main() -> int:
    iniciar_banco()
    await atualizar_cotacao()
    resultado = await obter_indice().indexar()
    custo = calcular(modelo_embedding(), resultado["tokensEmbedding"])
    if resultado["tokensEmbedding"]:
        custos.registrar("indexacao", custo, resultado["duracaoMs"])
    if resultado["novos"] or resultado["removidos"]:
        cache_semantico.limpar()

    print(f"Trechos na base: {resultado['trechos']}")
    print(f"  novos (pagaram embedding): {resultado['novos']}")
    print(f"  reaproveitados (custo zero): {resultado['reaproveitados']}")
    print(f"  removidos: {resultado['removidos']}")
    print(f"Tokens de embedding: {resultado['tokensEmbedding']:,} ({resultado['modelo']})")
    print(f"Custo: US$ {custo['usd']:.6f} = R$ {custo['brl']:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
