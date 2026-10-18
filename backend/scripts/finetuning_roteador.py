"""Fine-tuning do roteador por DESTILAÇÃO: prompt longo -> pesos do modelo.

Uso (de dentro de backend/), em ordem:
    python -m scripts.finetuning_roteador gerar      # professor rotula as mensagens-semente (~R$ 0,30)
    python -m scripts.finetuning_roteador validar    # confere o formato e estima o custo do treino
    python -m scripts.finetuning_roteador enviar     # sobe os arquivos e cria o job (COBRA o treino)
    python -m scripts.finetuning_roteador status     # acompanha; no fim mostra o nome do modelo ft:...
    python -m scripts.finetuning_roteador comparar   # base+prompt longo x afinado+prompt curto

A ideia:
- O roteador base precisa de ~1.100 tokens de instrução em TODA mensagem.
- Um PROFESSOR (gpt-4.1-mini com o prompt completo) rotula ~100 mensagens.
- Cada exemplo de treino leva só o PROMPT_CURTO (~25 tokens) + a entrada
  + o JSON do professor. O ALUNO (gpt-4.1-nano) aprende as regras e o formato.
- Depois: OPENAI_MODELO_ROTEADOR=ft:gpt-4.1-nano-2025-04-14:... no .env, e o
  roteador passa a usar o prompt curto sozinho (app.servicos.roteador).

Quando compensa (e quando não): o token do modelo afinado custa 2x o do base
(US$ 0,20 x US$ 0,10 por milhão na entrada). Com o prompt longo em cache
(75% de desconto), a economia em dinheiro é pequena. O ganho real costuma
ser outro: classificação mais consistente com os SEUS exemplos, menos
latência (menos tokens para ler) e um prompt que não precisa crescer a cada
regra nova. O comando `comparar` mede as duas coisas, custo e acerto.

ATENÇÃO: a OpenAI está encerrando a plataforma de fine-tuning (contas novas
já não têm acesso; contas antigas ainda podem criar jobs por alguns meses).
Se `enviar` der erro de permissão, é isso.
"""

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import date
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

from app.nucleo.banco import iniciar_banco  # noqa: E402
from app.nucleo.configuracao import PASTA_DADOS, modelo_roteador  # noqa: E402
from app.nucleo.precos import calcular, contar_tokens, custo_de_treino  # noqa: E402
from app.repositorios import custos  # noqa: E402
from app.servicos import openai_cliente, roteador  # noqa: E402
from app.servicos.cotacao import atualizar_cotacao  # noqa: E402

PASTA = PASTA_DADOS / "finetuning"
SEMENTES = PASTA / "mensagens_semente.txt"
TREINO = PASTA / "roteador_treino.jsonl"
VALIDACAO = PASTA / "roteador_validacao.jsonl"
JOB = PASTA / "job.json"
MODELO_BASE = "gpt-4.1-nano-2025-04-14"
EPOCAS = 3
# Data fixa nos exemplos: o modelo aprende a resolver "ontem" a partir da data
# que vier na entrada, não a decorar um dia.
DATA_EXEMPLOS = date(2026, 10, 17)


def _ler_sementes() -> list[tuple[list[dict[str, str]], str]]:
    casos = []
    for linha in SEMENTES.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        historico: list[dict[str, str]] = []
        if linha.startswith("Histórico:") and "|| Mensagem:" in linha:
            bruto, mensagem = linha[len("Histórico:"):].split("|| Mensagem:", 1)
            for fala in bruto.split(" | "):
                autor, _, texto = fala.strip().partition(":")
                historico.append({"autor": "cliente" if autor.strip() == "Cliente" else "assistente", "texto": texto.strip()})
            linha = mensagem.strip()
        casos.append((historico, linha))
    return casos


async def gerar(professor: str) -> None:
    await atualizar_cotacao()
    casos = _ler_sementes()
    exemplos, gasto = [], 0.0
    for numero, (historico, mensagem) in enumerate(casos, start=1):
        entrada = roteador.montar_entrada(mensagem, historico, DATA_EXEMPLOS)
        inicio = time.monotonic()
        decisao, uso = await openai_cliente.chat_json(
            [{"role": "system", "content": roteador.PROMPT_COMPLETO}, {"role": "user", "content": entrada}],
            professor, roteador.ESQUEMA, max_tokens=200)
        custo = calcular(professor, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0),
                         (uso.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        custos.registrar("dataset", custo, int((time.monotonic() - inicio) * 1000))
        gasto += custo["brl"]
        exemplos.append({"messages": [
            {"role": "system", "content": roteador.PROMPT_CURTO},
            {"role": "user", "content": entrada},
            {"role": "assistant", "content": json.dumps(decisao, ensure_ascii=False, separators=(",", ":"))},
        ]})
        print(f"{numero:>3}/{len(casos)} {decisao['intencao']:<26} {mensagem[:60]}")

    random.Random(42).shuffle(exemplos)
    corte = max(10, len(exemplos) // 5)
    for caminho, parte in ((VALIDACAO, exemplos[:corte]), (TREINO, exemplos[corte:])):
        caminho.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in parte) + "\n", encoding="utf-8")
    print(f"\n{len(exemplos) - corte} exemplos de treino, {corte} de validação. Custo do professor: R$ {gasto:.4f}")
    print("REVISE os JSONs gerados antes de enviar: o aluno aprende inclusive os erros do professor.")


def _ler_jsonl(caminho) -> list[dict[str, Any]]:
    return [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines() if l.strip()]


def validar() -> int:
    total_tokens, problemas = 0, 0
    for caminho in (TREINO, VALIDACAO):
        if not caminho.exists():
            print(f"Falta {caminho.name}. Rode `gerar` antes.")
            return 1
        exemplos = _ler_jsonl(caminho)
        for numero, exemplo in enumerate(exemplos, start=1):
            papeis = [m["role"] for m in exemplo.get("messages", [])]
            if papeis != ["system", "user", "assistant"]:
                print(f"{caminho.name}:{numero} papéis inesperados {papeis}")
                problemas += 1
                continue
            try:
                saida = json.loads(exemplo["messages"][2]["content"])
                assert set(saida) == set(roteador.ESQUEMA["required"])
                assert saida["intencao"] in roteador.INTENCOES
            except (ValueError, AssertionError):
                print(f"{caminho.name}:{numero} saída fora do esquema")
                problemas += 1
            if caminho == TREINO:
                total_tokens += sum(contar_tokens(m["content"]) + 4 for m in exemplo["messages"])
        print(f"{caminho.name}: {len(exemplos)} exemplos")

    estimativa = custo_de_treino(MODELO_BASE, total_tokens, EPOCAS)
    longo = contar_tokens(roteador.PROMPT_COMPLETO)
    curto = contar_tokens(roteador.PROMPT_CURTO)
    print(f"\nTokens de treino por época: {total_tokens:,} x {EPOCAS} épocas")
    print(f"Custo estimado do treino: US$ {estimativa['usd']:.4f} = R$ {estimativa['brl']:.4f}")
    print(f"Prompt do roteador: {longo} tokens (base) -> {curto} tokens (afinado)")
    print("Problemas encontrados:", problemas)
    return 1 if problemas else 0


def _cliente() -> httpx.Client:
    return httpx.Client(base_url=openai_cliente.URL_BASE, timeout=120,
                        headers={"Authorization": f"Bearer {openai_cliente._chave()}"})


def enviar() -> int:
    if validar():
        return 1
    with _cliente() as cliente:
        ids = {}
        for nome, caminho in (("treino", TREINO), ("validacao", VALIDACAO)):
            resposta = cliente.post("/files", data={"purpose": "fine-tune"},
                                    files={"file": (caminho.name, caminho.read_bytes(), "application/jsonl")})
            if resposta.status_code >= 400:
                print("Erro ao subir arquivo:", resposta.text)
                return 1
            ids[nome] = resposta.json()["id"]
        resposta = cliente.post("/fine_tuning/jobs", json={
            "model": MODELO_BASE, "training_file": ids["treino"], "validation_file": ids["validacao"],
            "suffix": "roteador-aurora", "hyperparameters": {"n_epochs": EPOCAS},
        })
        if resposta.status_code >= 400:
            print("Erro ao criar o job:", resposta.text)
            return 1
        job = resposta.json()
    JOB.write_text(json.dumps(job, indent=2), encoding="utf-8")
    print(f"Job criado: {job['id']} (status {job['status']}). Acompanhe com: python -m scripts.finetuning_roteador status")
    return 0


def status() -> int:
    if not JOB.exists():
        print("Nenhum job criado ainda.")
        return 1
    job_id = json.loads(JOB.read_text(encoding="utf-8"))["id"]
    with _cliente() as cliente:
        job = cliente.get(f"/fine_tuning/jobs/{job_id}").json()
        eventos = cliente.get(f"/fine_tuning/jobs/{job_id}/events", params={"limit": 8}).json().get("data", [])
    JOB.write_text(json.dumps(job, indent=2), encoding="utf-8")
    print(f"Job {job_id}: {job['status']}  tokens treinados: {job.get('trained_tokens')}")
    for evento in reversed(eventos):
        print("  ", evento.get("message"))
    if job.get("fine_tuned_model"):
        print(f"\nPronto! No backend/.env:\nOPENAI_MODELO_ROTEADOR={job['fine_tuned_model']}")
    return 0


async def comparar() -> int:
    """Acerto (contra o professor) e custo médio: base com prompt longo x afinado com prompt curto."""
    afinado = modelo_roteador()
    if not afinado.startswith("ft:"):
        print("Configure OPENAI_MODELO_ROTEADOR=ft:... no .env antes de comparar.")
        return 1
    await atualizar_cotacao()
    exemplos = _ler_jsonl(VALIDACAO)
    resultado = {"gpt-4.1-nano": {"acertos": 0, "brl": 0.0}, afinado: {"acertos": 0, "brl": 0.0}}
    campos = ("intencao", "precisa_base", "transferir_humano", "numero_pedido")
    for exemplo in exemplos:
        entrada = exemplo["messages"][1]["content"]
        esperado = json.loads(exemplo["messages"][2]["content"])
        for modelo in resultado:
            decisao, uso = await openai_cliente.chat_json(
                roteador.mensagens_do_roteador(modelo, entrada), modelo, roteador.ESQUEMA, max_tokens=150)
            custo = calcular(modelo, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0),
                             (uso.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
            custos.registrar("avaliacao", custo, 0)
            resultado[modelo]["brl"] += custo["brl"]
            resultado[modelo]["acertos"] += all(decisao[c] == esperado[c] for c in campos)
    for modelo, dados in resultado.items():
        print(f"{modelo[:50]:<50} acerto {dados['acertos']}/{len(exemplos)}  "
              f"custo médio R$ {dados['brl'] / len(exemplos):.6f} por chamada")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("comando", choices=["gerar", "validar", "enviar", "status", "comparar"])
    parser.add_argument("--professor", default="gpt-4.1-mini", help="modelo que rotula o dataset")
    args = parser.parse_args()
    iniciar_banco()
    if args.comando == "gerar":
        asyncio.run(gerar(args.professor))
        return 0
    if args.comando == "comparar":
        return asyncio.run(comparar())
    return {"validar": validar, "enviar": enviar, "status": status}[args.comando]()


if __name__ == "__main__":
    sys.exit(main())
