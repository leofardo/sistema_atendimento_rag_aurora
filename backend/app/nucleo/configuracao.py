"""Configurações lidas do .env, com padrões pensados para gastar pouco.

Cada número aqui é uma alavanca de custo ou de qualidade. Eles ficam juntos,
com o porquê de cada padrão, para dar para mexer sem caçar constante no código.
"""

import os
from pathlib import Path

RAIZ_BACKEND = Path(__file__).resolve().parents[2]
PASTA_DADOS = RAIZ_BACKEND / "dados"
PASTA_DOCUMENTOS = PASTA_DADOS / "documentos"
PASTA_INDICE = PASTA_DADOS / "indice"
CAMINHO_BANCO = PASTA_DADOS / "atendimento.db"


def texto_ambiente(nome: str, padrao: str) -> str:
    return (os.getenv(nome) or padrao).strip()


def numero_ambiente(nome: str, padrao: float) -> float:
    try:
        return float(os.getenv(nome, padrao))
    except (TypeError, ValueError):
        return padrao


def inteiro_ambiente(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, padrao))
    except (TypeError, ValueError):
        return padrao


# --- Modelos ---------------------------------------------------------------
# Roteamento por tarefa: o roteador só classifica e reescreve a pergunta (saída
# de ~60 tokens), então vai no nano — 4x mais barato que o mini. A resposta ao
# cliente, que precisa de redação cuidadosa, vai no mini.
def modelo_resposta() -> str:
    return texto_ambiente("OPENAI_MODELO_RESPOSTA", "gpt-4.1-mini")


def modelo_roteador() -> str:
    # Depois do fine-tuning, troque por "ft:gpt-4.1-nano-2025-04-14:...": o
    # roteador passa a usar o prompt curto (ver app.servicos.roteador).
    return texto_ambiente("OPENAI_MODELO_ROTEADOR", "gpt-4.1-nano")


def modelo_embedding() -> str:
    return texto_ambiente("OPENAI_MODELO_EMBEDDING", "text-embedding-3-small")


def dimensoes_embedding() -> int:
    # O text-embedding-3 aceita encurtar o vetor (Matryoshka). Não muda o preço
    # (que é por token de entrada), só o espaço em disco e a velocidade da
    # busca. Para uma base deste tamanho, 1536 cabe com folga.
    return inteiro_ambiente("OPENAI_DIMENSOES_EMBEDDING", 1536)


# --- RAG -------------------------------------------------------------------
def trechos_por_resposta() -> int:
    # Cada trecho custa ~150-400 tokens de entrada em TODA resposta com RAG.
    # 4 cobre quase toda pergunta de atendimento; mais que isso costuma trazer
    # trecho fraco que dilui a resposta.
    return inteiro_ambiente("RAG_TOP_K", 4)


def similaridade_minima() -> float:
    # Trecho com similaridade de cosseno abaixo disso não entra no prompt: é
    # token pago para atrapalhar. Calibrado com scripts/avaliar_rag.py: o pior
    # trecho certo teve 0,47 e perguntas fora do assunto chegaram a 0,35.
    return numero_ambiente("RAG_SIMILARIDADE_MINIMA", 0.40)


def peso_rrf_bm25() -> float:
    # Peso do BM25 na fusão com a busca vetorial (vetorial = 1). Medido com
    # scripts/avaliar_rag.py --pesos; 0 desliga o BM25.
    return numero_ambiente("RAG_PESO_BM25", 0.2)


def similaridade_cache() -> float:
    # Cache semântico: pergunta quase idêntica a uma já respondida reutiliza a
    # resposta. Alto de propósito — resposta errada servida do cache é pior que
    # pagar uma chamada.
    return numero_ambiente("CACHE_SIMILARIDADE", 0.95)


def validade_cache_horas() -> int:
    return inteiro_ambiente("CACHE_VALIDADE_HORAS", 168)


# --- Conversa ----------------------------------------------------------------
def mensagens_no_historico() -> int:
    # Só as últimas trocas vão inteiras; o que vem antes vira um resumo curto.
    return inteiro_ambiente("HISTORICO_MENSAGENS", 6)


def limite_caracteres_historico() -> int:
    # Respostas antigas longas são cortadas: o modelo só precisa do fio da meada.
    return inteiro_ambiente("HISTORICO_LIMITE_CARACTERES", 500)


def max_tokens_resposta() -> int:
    # Saída custa 4x a entrada. 2-4 frases cabem em ~150 tokens; o teto é rede
    # de segurança, não meta.
    return inteiro_ambiente("OPENAI_MAX_TOKENS_RESPOSTA", 450)


def ttl_conversa_segundos() -> int:
    return inteiro_ambiente("CONVERSA_TTL_SEGUNDOS", 60 * 60 * 24)


def url_redis() -> str:
    return texto_ambiente("REDIS_URL", "")


def origens_permitidas() -> list[str]:
    bruto = texto_ambiente("FRONTEND_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501")
    return [origem.strip() for origem in bruto.split(",") if origem.strip()]
