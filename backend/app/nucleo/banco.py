"""SQLite: o que precisa durar e ser consultado depois.

- ai_usage: livro-caixa de cada chamada à OpenAI (igual ao do Financeiro).
- turnos: uma linha por pergunta do cliente, com a ROTA que respondeu
  (regra, cache, rag, ...). É ela que mostra quanto cada atalho economizou.
- pedidos: pedidos fictícios para o atendimento consultar.
- conversas/mensagens: memória da conversa quando não há Redis
  (ver app.repositorios.memoria).
"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.nucleo.configuracao import CAMINHO_BANCO

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sessao TEXT,
    turno_id INTEGER,
    operacao TEXT NOT NULL,
    modelo TEXT NOT NULL,
    tokens_entrada INTEGER NOT NULL DEFAULT 0,
    tokens_saida INTEGER NOT NULL DEFAULT 0,
    tokens_cache INTEGER NOT NULL DEFAULT 0,
    usd REAL NOT NULL DEFAULT 0,
    cotacao REAL NOT NULL,
    medido_pela_api INTEGER NOT NULL DEFAULT 1,
    duracao_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_ai_usage_criado ON ai_usage(created_at);
CREATE INDEX IF NOT EXISTS ix_ai_usage_turno ON ai_usage(turno_id);

CREATE TABLE IF NOT EXISTS turnos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sessao TEXT NOT NULL,
    pergunta TEXT NOT NULL,
    rota TEXT,
    intencao TEXT,
    trechos INTEGER NOT NULL DEFAULT 0,
    duracao_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_turnos_criado ON turnos(created_at);

CREATE TABLE IF NOT EXISTS conversas (
    sessao TEXT PRIMARY KEY,
    protocolo TEXT NOT NULL,
    resumo TEXT NOT NULL DEFAULT '',
    resumidas INTEGER NOT NULL DEFAULT 0,
    atualizada_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mensagens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sessao TEXT NOT NULL,
    autor TEXT NOT NULL,
    texto TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_mensagens_sessao ON mensagens(sessao, id);

CREATE TABLE IF NOT EXISTS pedidos (
    numero TEXT PRIMARY KEY,
    cliente TEXT NOT NULL,
    canal TEXT NOT NULL,
    status TEXT NOT NULL,
    itens TEXT NOT NULL,
    valor REAL NOT NULL,
    pagamento TEXT NOT NULL,
    data_compra TEXT NOT NULL,
    previsao_entrega TEXT,
    data_entrega TEXT,
    rastreio TEXT
);
"""

# Datas relativas a hoje, para os exemplos continuarem fazendo sentido
# (dentro/fora do prazo de arrependimento) em qualquer dia em que o app rodar.
PEDIDOS_FICTICIOS = [
    ("100231", "Marina", "site", "entregue", "Air fryer 5L", 489.90, "pix", -9, -6, -4, "AU100231BR"),
    ("100245", "Rafael", "app", "saiu para entrega", "Smart TV 50 polegadas", 2599.00, "cartão 10x", -4, 0, None, "AU100245BR"),
    ("100250", "Joana", "site", "em separação", "Liquidificador 1200W", 219.90, "boleto", -1, 4, None, None),
    ("100262", "Carlos", "telefone", "entregue", "Geladeira frost free 400L", 3899.00, "cartão 10x", -40, -35, -35, "AU100262BR"),
    ("100270", "Beatriz", "app", "enviado", "Fone Bluetooth", 299.00, "pix", -3, 2, None, "AU100270BR"),
    ("100281", "Paulo", "loja", "entregue", "Notebook 15 polegadas", 3499.00, "cartão 10x", -12, None, -12, None),
    ("100293", "Lúcia", "site", "aguardando pagamento", "Cafeteira expresso", 649.00, "boleto", 0, 5, None, None),
    ("100301", "André", "site", "entregue", "Ventilador de coluna", 259.90, "pix", -15, -10, -12, "AU100301BR"),
]


@contextmanager
def obter_conexao() -> Iterator[sqlite3.Connection]:
    conexao = sqlite3.connect(CAMINHO_BANCO)
    conexao.row_factory = sqlite3.Row
    try:
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


# Colunas acrescentadas depois da primeira versão: criadas se o banco for antigo.
COLUNAS_NOVAS = [
    ("mensagens", "meta", "TEXT"),          # custos, rota e fontes da resposta (histórico)
    ("conversas", "titulo", "TEXT"),        # nome da conversa no menu lateral
    ("turnos", "protocolo", "TEXT"),        # protocolo nas tabelas de custo
]


def _migrar(conexao: sqlite3.Connection) -> None:
    for tabela, coluna, tipo in COLUNAS_NOVAS:
        existentes = {linha[1] for linha in conexao.execute(f"PRAGMA table_info({tabela})")}
        if coluna not in existentes:
            conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
    # Turnos antigos: protocolo vindo da conversa.
    conexao.execute("""UPDATE turnos SET protocolo = (SELECT protocolo FROM conversas c WHERE c.sessao = turnos.sessao)
                       WHERE protocolo IS NULL""")
    # Conversas antigas: título = primeira pergunta do cliente.
    conexao.execute("""UPDATE conversas SET titulo = (SELECT substr(texto, 1, 48) FROM mensagens m
                       WHERE m.sessao = conversas.sessao AND m.autor = 'cliente' ORDER BY m.id LIMIT 1)
                       WHERE titulo IS NULL""")


def iniciar_banco() -> None:
    CAMINHO_BANCO.parent.mkdir(parents=True, exist_ok=True)
    with obter_conexao() as conexao:
        conexao.executescript(ESQUEMA)
        _migrar(conexao)
        if conexao.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0] == 0:
            conexao.executemany(
                """
                INSERT INTO pedidos VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    date('now', ? || ' days'),
                    CASE WHEN ? IS NULL THEN NULL ELSE date('now', ? || ' days') END,
                    CASE WHEN ? IS NULL THEN NULL ELSE date('now', ? || ' days') END,
                    ?
                )
                """,
                [
                    (num, cli, canal, status, itens, valor, pag, compra, prev, prev, ent, ent, rastreio)
                    for num, cli, canal, status, itens, valor, pag, compra, prev, ent, rastreio in PEDIDOS_FICTICIOS
                ],
            )
