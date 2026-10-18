"""Livro-caixa: cada chamada à OpenAI e cada turno de conversa.

Duas escalas, como no Financeiro: o resumo agregado responde "quanto já
custou e onde", e a lista chamada a chamada responde "por que esta resposta
custou o triplo da anterior?".

A tabela `turnos` acrescenta a pergunta que importa num atendimento: QUAL
ROTA respondeu (regra, cache, RAG...) e para qual INTENÇÃO. É assim que se
descobre, por exemplo, que "status de pedido" é metade do gasto e merece
uma resposta por regra.
"""

from typing import Any

from app.nucleo.banco import obter_conexao
from app.nucleo.precos import ROTULOS_DE_OPERACAO, cotacao_do_dolar, preco_do_modelo

ROTULOS_DE_ROTA = {
    "regra": "Resposta pronta (regra)",
    "cache": "Cache semântico",
    "rag": "RAG (base de conhecimento)",
    "sem_base": "Sem base (conversa)",
    "nao_encontrado": "Nada relevante na base",
    "humano": "Transferido para humano",
    "erro": "Erro",
}


def abrir_turno(sessao: str, pergunta: str, protocolo: str | None = None) -> int:
    with obter_conexao() as conexao:
        cursor = conexao.execute("INSERT INTO turnos (sessao, pergunta, protocolo) VALUES (?, ?, ?)",
                                 (sessao, pergunta[:300], protocolo))
        return int(cursor.lastrowid)


def metas_dos_turnos(sessao: str) -> list[dict[str, Any]]:
    """Rota, intenção e custos de cada turno da conversa, reconstruídos do livro-caixa.

    Para respostas gravadas antes de a mensagem guardar seus metadados: o
    livro-caixa sempre teve tudo, só não estava ligado à mensagem.
    """
    with obter_conexao() as conexao:
        turnos = conexao.execute(
            "SELECT * FROM turnos WHERE sessao = ? AND rota IS NOT NULL ORDER BY id", (sessao,)).fetchall()
        chamadas = conexao.execute(
            "SELECT * FROM ai_usage WHERE sessao = ? AND turno_id IS NOT NULL ORDER BY id", (sessao,)).fetchall()
    por_turno: dict[int, list[dict[str, Any]]] = {}
    for c in chamadas:
        usd_entrada, usd_saida = _rateio(c)
        por_turno.setdefault(c["turno_id"], []).append({
            "operacao": c["operacao"], "rotuloOperacao": ROTULOS_DE_OPERACAO.get(c["operacao"], c["operacao"]),
            "modelo": c["modelo"], "entrada": c["tokens_entrada"], "cache": c["tokens_cache"], "saida": c["tokens_saida"],
            "usd": c["usd"], "brl": c["usd"] * c["cotacao"], "usdEntrada": usd_entrada, "usdSaida": usd_saida,
            "brlEntrada": usd_entrada * c["cotacao"], "brlSaida": usd_saida * c["cotacao"],
        })
    return [
        {"rota": t["rota"], "rotuloRota": ROTULOS_DE_ROTA.get(t["rota"], t["rota"]), "intencao": t["intencao"],
         "custos": por_turno.get(t["id"], []), "fontes": [], "cache": None, "duracaoMs": t["duracao_ms"],
         "totalBrl": sum(c["brl"] for c in por_turno.get(t["id"], []))}
        for t in turnos
    ]


def custo_por_sessao(sessoes: list[str]) -> dict[str, float]:
    """R$ gasto em cada conversa (para o menu lateral)."""
    if not sessoes:
        return {}
    marcadores = ",".join("?" * len(sessoes))
    with obter_conexao() as conexao:
        linhas = conexao.execute(
            f"SELECT sessao, SUM(usd * cotacao) AS brl FROM ai_usage WHERE sessao IN ({marcadores}) GROUP BY sessao",
            sessoes,
        ).fetchall()
    return {l["sessao"]: l["brl"] or 0.0 for l in linhas}


def fechar_turno(turno_id: int, rota: str, intencao: str | None, trechos: int, duracao_ms: int) -> None:
    with obter_conexao() as conexao:
        conexao.execute(
            "UPDATE turnos SET rota = ?, intencao = ?, trechos = ?, duracao_ms = ? WHERE id = ?",
            (rota, intencao, trechos, duracao_ms, turno_id),
        )


def registrar(
    operacao: str, custo: dict[str, Any], duracao_ms: int, sessao: str | None = None, turno_id: int | None = None
) -> dict[str, Any]:
    with obter_conexao() as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO ai_usage (sessao, turno_id, operacao, modelo, tokens_entrada, tokens_saida,
                                  tokens_cache, usd, cotacao, medido_pela_api, duracao_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sessao, turno_id, operacao, custo["modelo"], custo["entrada"], custo["saida"], custo["cache"],
                custo["usd"], cotacao_do_dolar(), 1 if custo.get("medidoPelaApi", True) else 0, duracao_ms,
            ),
        )
    return {**custo, "id": cursor.lastrowid, "operacao": operacao,
            "rotuloOperacao": ROTULOS_DE_OPERACAO.get(operacao, operacao), "duracaoMs": duracao_ms}


def _recorte(coluna: str, desde: str | None, ate: str | None) -> tuple[str, list[Any]]:
    """`desde`/`ate` em UTC 'YYYY-MM-DD HH:MM:SS' (created_at é UTC)."""
    clausula, parametros = "", []
    if desde:
        clausula += f" AND {coluna} >= ?"
        parametros.append(desde)
    if ate:
        clausula += f" AND {coluna} <= ?"
        parametros.append(ate)
    return clausula, parametros


def _rateio(linha: Any) -> tuple[float, float]:
    """Entrada x saída recalculadas da tabela de preços (o total gravado é o oficial)."""
    preco = preco_do_modelo(linha["modelo"])
    if not preco:
        return 0.0, 0.0
    cheia = max(linha["tokens_entrada"] - linha["tokens_cache"], 0)
    entrada = (cheia * preco["entrada"] + linha["tokens_cache"] * preco["cache"]) / 1_000_000
    return entrada, linha["tokens_saida"] * preco["saida"] / 1_000_000


def resumo(desde: str | None = None, ate: str | None = None) -> dict[str, Any]:
    clausula, parametros = _recorte("created_at", desde, ate)
    with obter_conexao() as conexao:
        por_operacao = conexao.execute(
            f"""
            SELECT operacao, modelo, COUNT(*) AS chamadas, SUM(tokens_entrada) AS entrada,
                   SUM(tokens_cache) AS cache, SUM(tokens_saida) AS saida,
                   SUM(usd) AS usd, SUM(usd * cotacao) AS brl
            FROM ai_usage WHERE 1 = 1{clausula}
            GROUP BY operacao, modelo ORDER BY usd DESC
            """,
            parametros,
        ).fetchall()

        clausula_t, parametros_t = _recorte("t.created_at", desde, ate)
        por_rota = conexao.execute(
            f"""
            SELECT t.rota, COUNT(DISTINCT t.id) AS turnos, COALESCE(SUM(u.usd), 0) AS usd,
                   COALESCE(SUM(u.usd * u.cotacao), 0) AS brl
            FROM turnos t LEFT JOIN ai_usage u ON u.turno_id = t.id
            WHERE t.rota IS NOT NULL{clausula_t}
            GROUP BY t.rota ORDER BY turnos DESC
            """,
            parametros_t,
        ).fetchall()
        por_intencao = conexao.execute(
            f"""
            SELECT COALESCE(t.intencao, 'sem classificação') AS intencao, COUNT(DISTINCT t.id) AS turnos,
                   COALESCE(SUM(u.usd), 0) AS usd, COALESCE(SUM(u.usd * u.cotacao), 0) AS brl
            FROM turnos t LEFT JOIN ai_usage u ON u.turno_id = t.id
            WHERE t.rota IS NOT NULL{clausula_t}
            GROUP BY 1 ORDER BY usd DESC
            """,
            parametros_t,
        ).fetchall()

    operacoes = [
        {
            "operacao": l["operacao"], "rotulo": ROTULOS_DE_OPERACAO.get(l["operacao"], l["operacao"]),
            "modelo": l["modelo"], "chamadas": l["chamadas"], "entrada": l["entrada"] or 0,
            "cache": l["cache"] or 0, "saida": l["saida"] or 0, "usd": l["usd"] or 0.0, "brl": l["brl"] or 0.0,
        }
        for l in por_operacao
    ]
    turnos = sum(l["turnos"] for l in por_rota)
    with obter_conexao() as conexao:
        atendimento = conexao.execute(
            f"SELECT COALESCE(SUM(usd * cotacao), 0) FROM ai_usage WHERE sessao IS NOT NULL{clausula}", parametros
        ).fetchone()[0]
    total_brl = sum(o["brl"] for o in operacoes)
    return {
        # Atendimento = chamadas feitas durante conversas (inclui o resumo da conversa).
        # O resto é indexação da base, avaliações e geração de dataset — custo de construir, não de atender.
        "atendimentoBrl": atendimento,
        "construcaoBrl": total_brl - atendimento,
        "cotacao": cotacao_do_dolar(),
        "totalUsd": sum(o["usd"] for o in operacoes),
        "totalBrl": sum(o["brl"] for o in operacoes),
        "totalChamadas": sum(o["chamadas"] for o in operacoes),
        "totalTokensEntrada": sum(o["entrada"] for o in operacoes),
        "totalTokensCache": sum(o["cache"] for o in operacoes),
        "totalTokensSaida": sum(o["saida"] for o in operacoes),
        "totalTurnos": turnos,
        "porOperacao": operacoes,
        "porRota": [
            {"rota": l["rota"], "rotulo": ROTULOS_DE_ROTA.get(l["rota"], l["rota"]), "turnos": l["turnos"],
             "usd": l["usd"], "brl": l["brl"]}
            for l in por_rota
        ],
        "porIntencao": [{"intencao": l["intencao"], "turnos": l["turnos"], "usd": l["usd"], "brl": l["brl"]} for l in por_intencao],
    }


def listar_chamadas(limite: int = 200, desde: str | None = None, ate: str | None = None) -> list[dict[str, Any]]:
    clausula, parametros = _recorte("u.created_at", desde, ate)
    with obter_conexao() as conexao:
        linhas = conexao.execute(
            f"""
            SELECT u.*, t.pergunta, t.rota, t.intencao, t.protocolo FROM ai_usage u
            LEFT JOIN turnos t ON t.id = u.turno_id
            WHERE 1 = 1{clausula} ORDER BY u.id DESC LIMIT ?
            """,
            [*parametros, limite],
        ).fetchall()
    chamadas = []
    for l in linhas:
        usd_entrada, usd_saida = _rateio(l)
        chamadas.append({
            "id": l["id"], "quando": l["created_at"], "sessao": l["sessao"], "turnoId": l["turno_id"],
            "operacao": l["operacao"], "rotuloOperacao": ROTULOS_DE_OPERACAO.get(l["operacao"], l["operacao"]),
            "modelo": l["modelo"], "entrada": l["tokens_entrada"], "cache": l["tokens_cache"],
            "saida": l["tokens_saida"], "usd": l["usd"], "brl": l["usd"] * l["cotacao"],
            "brlEntrada": usd_entrada * l["cotacao"], "brlSaida": usd_saida * l["cotacao"],
            "medidoPelaApi": bool(l["medido_pela_api"]), "duracaoMs": l["duracao_ms"],
            "pergunta": l["pergunta"], "rota": l["rota"], "intencao": l["intencao"], "protocolo": l["protocolo"],
        })
    return chamadas


def listar_turnos(limite: int = 200, desde: str | None = None, ate: str | None = None) -> list[dict[str, Any]]:
    clausula, parametros = _recorte("t.created_at", desde, ate)
    with obter_conexao() as conexao:
        linhas = conexao.execute(
            f"""
            SELECT t.*, COUNT(u.id) AS chamadas, COALESCE(SUM(u.tokens_entrada), 0) AS entrada,
                   COALESCE(SUM(u.tokens_cache), 0) AS cache, COALESCE(SUM(u.tokens_saida), 0) AS saida,
                   COALESCE(SUM(u.usd), 0) AS usd, COALESCE(SUM(u.usd * u.cotacao), 0) AS brl
            FROM turnos t LEFT JOIN ai_usage u ON u.turno_id = t.id
            WHERE t.rota IS NOT NULL{clausula}
            GROUP BY t.id ORDER BY t.id DESC LIMIT ?
            """,
            [*parametros, limite],
        ).fetchall()
    return [
        {
            "id": l["id"], "quando": l["created_at"], "sessao": l["sessao"], "protocolo": l["protocolo"],
            "pergunta": l["pergunta"],
            "rota": l["rota"], "rotuloRota": ROTULOS_DE_ROTA.get(l["rota"], l["rota"]), "intencao": l["intencao"],
            "trechos": l["trechos"], "chamadas": l["chamadas"], "entrada": l["entrada"], "cache": l["cache"],
            "saida": l["saida"], "usd": l["usd"], "brl": l["brl"], "duracaoMs": l["duracao_ms"],
        }
        for l in linhas
    ]
