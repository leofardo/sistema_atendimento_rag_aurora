"""Avaliação do RAG — sem medir, não dá para saber se a economia derrubou a qualidade.

Uso (de dentro de backend/):
    python -m scripts.avaliar_rag              # só a busca (custa ~R$ 0,0001)
    python -m scripts.avaliar_rag --roteador   # reescreve as perguntas com o roteador antes (nano)
    python -m scripts.avaliar_rag --juiz       # também gera a resposta e um juiz (nano) dá a nota

1. BUSCA (o que mais importa e o mais barato de medir): para cada pergunta
   de avaliacao/perguntas.json há o(s) trecho(s) que DEVERIAM vir. Mede:
   - acerto@k: algum trecho esperado veio entre os k primeiros?
   - MRR: 1/posição do primeiro trecho certo (1,0 = sempre em 1º lugar).
   Compara três modos — só vetorial, só BM25 e HÍBRIDO (RRF) — para mostrar
   por que a busca híbrida vale a pena.
   Também mostra a similaridade do trecho certo x a do melhor trecho errado:
   é com ela que se calibra RAG_SIMILARIDADE_MINIMA.

2. JUIZ (opcional, como o QAEvalChain do curso da Alura): gera a resposta
   com o mesmo prompt do atendimento e pede a um modelo nano para comparar
   com a resposta esperada: CORRETA / PARCIAL / INCORRETA.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from app.nucleo.banco import iniciar_banco  # noqa: E402
from app.nucleo.configuracao import (  # noqa: E402
    RAIZ_BACKEND,
    modelo_embedding,
    modelo_resposta,
    peso_rrf_bm25,
    trechos_por_resposta,
)
from app.nucleo.precos import calcular  # noqa: E402
from app.rag.indice import CANDIDATOS, fundir_rrf, obter_indice  # noqa: E402
from app.repositorios import custos  # noqa: E402
from app.servicos import openai_cliente, prompt_resposta, roteador  # noqa: E402
from app.servicos.cotacao import atualizar_cotacao  # noqa: E402

PROMPT_JUIZ = """Você avalia respostas de um assistente de atendimento. Compare a RESPOSTA OBTIDA com a RESPOSTA ESPERADA para a PERGUNTA.
CORRETA: contém a informação essencial da esperada, sem contradizê-la. PARCIAL: acerta parte ou omite algo importante. INCORRETA: contradiz, inventa ou não responde."""
ESQUEMA_JUIZ = {
    "type": "object", "additionalProperties": False, "required": ["nota", "motivo"],
    "properties": {"nota": {"type": "string", "enum": ["CORRETA", "PARCIAL", "INCORRETA"]}, "motivo": {"type": "string"}},
}


def _bate(meta: dict, esperados: list[list[str]]) -> bool:
    """Artigo bate exato ("Art. 1" não é "Art. 12"), inclusive as partes de um artigo longo;
    seção do manual bate pelo começo do título."""
    def igual(referencia: str, esperada: str) -> bool:
        if esperada.startswith("Art."):
            return referencia == esperada or referencia.startswith(esperada + " (")
        return referencia.startswith(esperada)

    return any(meta["fonte"] == fonte and igual(meta["referencia"], ref) for fonte, ref in esperados)


def _posicao(ids: list[str], metas: dict[str, dict], esperados: list[list[str]]) -> int | None:
    for posicao, trecho_id in enumerate(ids, start=1):
        if _bate(metas[trecho_id], esperados):
            return posicao
    return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roteador", action="store_true", help="reescrever as perguntas com o roteador antes de buscar")
    parser.add_argument("--juiz", action="store_true", help="gerar respostas e avaliar com um modelo juiz")
    parser.add_argument("--pesos", action="store_true", help="comparar vários pesos do BM25 na fusão")
    args = parser.parse_args()

    iniciar_banco()
    await atualizar_cotacao()
    indice = obter_indice()
    if indice.total() == 0:
        print("Base vazia. Rode antes: python -m scripts.indexar")
        return 1

    casos = json.loads((RAIZ_BACKEND / "avaliacao" / "perguntas.json").read_text(encoding="utf-8"))
    metas = {t["id"]: t for t in indice.listar_trechos()}
    k = trechos_por_resposta()
    gasto = 0.0

    consultas = [c["pergunta"] for c in casos]
    if args.roteador:
        consultas = []
        for caso in casos:
            decisao, custo, duracao = await roteador.rotear(caso["pergunta"], [], date.today())
            custos.registrar("avaliacao", custo, duracao)
            gasto += custo["brl"]
            consultas.append(decisao["consulta"] or caso["pergunta"])

    inicio = time.monotonic()
    vetores, tokens = await openai_cliente.embeddings(consultas)
    custo = calcular(modelo_embedding(), tokens)
    custos.registrar("avaliacao", custo, int((time.monotonic() - inicio) * 1000))
    gasto += custo["brl"]

    colecao = indice.colecao()
    modos = {"vetorial": [], "bm25": [], f"hibrido (peso BM25 {peso_rrf_bm25()})": [], "atendimento (real)": []}
    sim_certo, sim_errado = [], []
    listas: list[tuple[dict, list[str], list[str]]] = []
    print(f"\n{'pergunta':<62} {'vet':>4} {'bm25':>4} {'hib':>4} {'real':>4}")
    for caso, consulta, vetor in zip(casos, consultas, vetores):
        resposta = colecao.query(query_embeddings=[vetor], n_results=CANDIDATOS, include=["distances"])
        ids_vet = resposta["ids"][0]
        dist = dict(zip(ids_vet, resposta["distances"][0]))
        ids_bm25 = indice._buscar_bm25(consulta)
        listas.append((caso, ids_vet, ids_bm25))
        pontos = fundir_rrf(ids_vet, ids_bm25)
        ids_hib = sorted(pontos, key=pontos.get, reverse=True)

        # O que o atendimento de fato manda ao prompt: fusão + artigo citado + limiar + top-k.
        ids_real = [r.trecho_id for r in indice.buscar(consulta, vetor, k=k, texto_original=caso["pergunta"])]
        posicoes = dict(zip(modos, (_posicao(ids_vet, metas, caso["esperado"]),
                                    _posicao(ids_bm25, metas, caso["esperado"]),
                                    _posicao(ids_hib, metas, caso["esperado"]),
                                    _posicao(ids_real, metas, caso["esperado"]))))
        for modo, posicao in posicoes.items():
            modos[modo].append(posicao)

        certos = [1 - d for i, d in dist.items() if _bate(metas[i], caso["esperado"])]
        errados = [1 - d for i, d in dist.items() if not _bate(metas[i], caso["esperado"])]
        if certos:
            sim_certo.append(max(certos))
        if errados:
            sim_errado.append(max(errados))

        fmt = lambda p: "-" if p is None else str(p)  # noqa: E731
        print(f"{caso['pergunta'][:62]:<62} " + " ".join(f"{fmt(p):>4}" for p in posicoes.values()))

    def linha(modo: str, posicoes: list[int | None]) -> None:
        n = len(posicoes)
        a1 = sum(1 for p in posicoes if p == 1) / n
        ak = sum(1 for p in posicoes if p and p <= k) / n
        mrr = sum(1 / p for p in posicoes if p) / n
        print(f"{modo:<28} {a1:>9.0%} {ak:>9.0%} {mrr:>6.3f}")

    print(f"\nPosição do primeiro trecho certo (- = fora dos {CANDIDATOS} candidatos). k usado no atendimento = {k}.")
    print(f"{'modo':<28} {'acerto@1':>9} {'acerto@' + str(k):>9} {'MRR':>6}")
    for modo, posicoes in modos.items():
        linha(modo, posicoes)

    if args.pesos:
        print("\nVarredura do peso do BM25 na fusão (RAG_PESO_BM25):")
        for peso in (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0):
            posicoes = []
            for caso, ids_vet, ids_bm25 in listas:
                pontos = fundir_rrf(ids_vet, ids_bm25, peso)
                posicoes.append(_posicao(sorted(pontos, key=pontos.get, reverse=True), metas, caso["esperado"]))
            linha(f"peso {peso}", posicoes)

    if sim_certo and sim_errado:
        print("\nSimilaridade de cosseno (para calibrar RAG_SIMILARIDADE_MINIMA):")
        print(f"  melhor trecho CERTO: mín {min(sim_certo):.3f} | mediana {statistics.median(sim_certo):.3f}")
        print(f"  melhor trecho ERRADO: mediana {statistics.median(sim_errado):.3f}")
        print("  O limiar deve ficar abaixo do mínimo dos certos: ele serve para barrar pergunta sem resposta na base,")
        print("  não para escolher entre trechos (isso é trabalho do ranking).")

    if args.juiz:
        notas = []
        for caso, consulta, vetor in zip(casos, consultas, vetores):
            trechos = indice.buscar(consulta, vetor)
            mensagens = prompt_resposta.montar_mensagens(caso["pergunta"], "AVALIACAO", date.today(), "", [], 500, [], trechos)
            texto, uso = await openai_cliente.chat_texto(mensagens, modelo_resposta(), max_tokens=450)
            c1 = calcular(modelo_resposta(), uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0),
                          (uso.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
            custos.registrar("avaliacao", c1, 0)
            avaliacao, uso_j = await openai_cliente.chat_json(
                [{"role": "system", "content": PROMPT_JUIZ},
                 {"role": "user", "content": f"PERGUNTA: {caso['pergunta']}\nRESPOSTA ESPERADA: {caso['resposta']}\nRESPOSTA OBTIDA: {texto}"}],
                "gpt-4.1-nano", ESQUEMA_JUIZ, max_tokens=120)
            c2 = calcular("gpt-4.1-nano", uso_j.get("prompt_tokens", 0), uso_j.get("completion_tokens", 0))
            custos.registrar("avaliacao", c2, 0)
            gasto += c1["brl"] + c2["brl"]
            notas.append(avaliacao["nota"])
            print(f"\n[{avaliacao['nota']}] {caso['pergunta']}\n  {texto}\n  juiz: {avaliacao['motivo']}")
        print(f"\nJuiz: {notas.count('CORRETA')} corretas, {notas.count('PARCIAL')} parciais, "
              f"{notas.count('INCORRETA')} incorretas de {len(notas)}.")

    print(f"\nCusto desta avaliação: R$ {gasto:.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
