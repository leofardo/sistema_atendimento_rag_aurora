"""Gasto com IA: onde o dinheiro vai, e quanto os atalhos economizaram.

O gasto total separa ATENDIMENTO (chamadas feitas durante as conversas) de
INDEXAÇÃO E TESTES (embedding da base, avaliações, dataset de fine-tuning):
o primeiro é custo de operar, o segundo é custo de construir.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import api
import ui
from formatos import BRASILIA, brl, data_hora, inteiro, md, porcentagem, usd

PERIODOS = {"Hoje": 0, "7 dias": 7, "30 dias": 30, "Tudo": None}


@st.cache_data(ttl=20, show_spinner=False)
def carregar(desde: str | None) -> tuple[dict, list, list]:
    """As três consultas do período. 20 s de cache: trocar de página e voltar não refaz tudo."""
    return (api.get("/api/custos/resumo", desde=desde),
            api.get("/api/custos/turnos", desde=desde, limite=300)["turnos"],
            api.get("/api/custos/chamadas", desde=desde, limite=500)["chamadas"])


esquerda, meio, direita = st.columns([3, 2, 1], vertical_alignment="bottom")
with esquerda:
    ui.cabecalho("Controle de custos", "Custos e tokens", "Cada chamada à OpenAI, em tokens, dólar e real.")
with meio:
    periodo = st.segmented_control("Período", list(PERIODOS), default="Hoje", label_visibility="collapsed") or "Hoje"
with direita:
    if st.button("Atualizar", icon=":material/refresh:", width="stretch"):
        carregar.clear()
        ui.configuracao.clear()

config = ui.configuracao()
ui.barra_de_status(config)

desde = None
if PERIODOS[periodo] is not None:
    # O corte nasce no horário de Brasília e vai em UTC, que é como o banco grava.
    inicio_local = datetime.now(BRASILIA).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_local -= timedelta(days=PERIODOS[periodo])
    desde = inicio_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

try:
    resumo, turnos, chamadas = carregar(desde)
except api.ErroApi as erro:
    st.error(str(erro))
    st.stop()

# --- indicadores ----------------------------------------------------------
total_turnos = resumo["totalTurnos"]
baratas = sum(r["turnos"] for r in resumo["porRota"] if r["rota"] in ("regra", "cache", "humano", "nao_encontrado"))
entrada, cache, saida = resumo["totalTokensEntrada"], resumo["totalTokensCache"], resumo["totalTokensSaida"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Gasto total", brl(resumo["totalBrl"]), border=True,
          help=f"{usd(resumo['totalUsd'])}. Soma do atendimento com indexação e testes.")
c2.metric("Atendimento", brl(resumo["atendimentoBrl"]), border=True,
          help="Chamadas feitas durante as conversas: roteador, embedding da pergunta, resposta e resumo.")
c3.metric("Indexação e testes", brl(resumo["construcaoBrl"]), border=True,
          help="Embedding da base de conhecimento, avaliações do RAG e dataset de fine-tuning.")
c4.metric("Cotação do dólar", f"R$ {resumo['cotacao']:.4f}".replace(".", ","), border=True,
          help=f"Fonte: {resumo['origemCotacao']}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Mensagens atendidas", inteiro(total_turnos), border=True)
c2.metric("Custo médio por mensagem",
          brl(resumo["atendimentoBrl"] / total_turnos) if total_turnos else "—", border=True,
          help="Gasto do atendimento ÷ mensagens.")
c3.metric("Sem o modelo de resposta", porcentagem(baratas, total_turnos), border=True,
          help="Mensagens resolvidas por regra, cache semântico, transferência ou resposta curta pronta.")
c4.metric("Entrada em cache", porcentagem(cache, entrada), border=True,
          help=f"{inteiro(cache)} de {inteiro(entrada)} tokens de entrada saíram com 75% de desconto "
               f"(prompt fixo repetido). Tokens de saída: {inteiro(saida)}.")

# --- gráficos -------------------------------------------------------------
esquerda, direita = st.columns([3, 2])
with esquerda, st.container(border=True, height=360):
    ui.titulo_card("Gasto por dia", "Em reais, no horário de Brasília")
    if chamadas:
        por_dia = pd.DataFrame(chamadas)
        por_dia["dia"] = pd.to_datetime(por_dia["quando"], utc=True).dt.tz_convert(BRASILIA).dt.strftime("%d/%m")
        st.bar_chart(por_dia.groupby("dia")["brl"].sum().rename("R$"), height=265, color="#3B82F6")
    else:
        st.caption("Sem chamadas no período.")
with direita, st.container(border=True, height=360):
    ui.titulo_card("Gasto por intenção do cliente", "Qual assunto pesa mais")
    if resumo["porIntencao"]:
        dados = pd.DataFrame([{"intenção": i["intencao"], "R$": i["brl"]} for i in resumo["porIntencao"]]).set_index("intenção")
        st.bar_chart(dados, horizontal=True, height=265, color="#22C55E")
    else:
        st.caption("Sem mensagens no período.")

# --- tabelas ----------------------------------------------------------------
with st.container(border=True):
    ui.titulo_card("Por rota de resposta", "Por onde cada mensagem saiu e quanto custou")
    if resumo["porRota"]:
        st.dataframe(pd.DataFrame([
            {"Rota": r["rotulo"], "Mensagens": r["turnos"],
             "Participação": 100 * r["turnos"] / total_turnos if total_turnos else 0,
             "Gasto": brl(r["brl"]), "Médio": brl(r["brl"] / r["turnos"] if r["turnos"] else 0, 5)}
            for r in resumo["porRota"]
        ]), hide_index=True, width="stretch", column_config={
            "Rota": st.column_config.TextColumn(width="medium"),
            "Participação": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100, width="small"),
        })
    else:
        st.caption("Nenhuma mensagem no período.")
with st.container(border=True):
    ui.titulo_card("Por operação", "Cada etapa e o modelo que ela usa")
    if resumo["porOperacao"]:
        st.dataframe(pd.DataFrame([
            {"Operação": o["rotulo"], "Modelo": o["modelo"], "Chamadas": o["chamadas"],
             "Tokens de entrada": o["entrada"], "Em cache": o["cache"], "Tokens de saída": o["saida"],
             "Gasto": brl(o["brl"])}
            for o in resumo["porOperacao"]
        ]), hide_index=True, width="stretch")
    else:
        st.caption("Nenhuma chamada no período.")

with st.container(border=True):
    ui.titulo_card("Mensagem a mensagem", "Cada pergunta do cliente, com o protocolo do atendimento")
    if turnos:
        st.dataframe(pd.DataFrame([
            {"Quando": data_hora(t["quando"]), "Protocolo": t["protocolo"] or "—", "Pergunta": t["pergunta"],
             "Rota": t["rotuloRota"], "Intenção": t["intencao"], "Trechos": t["trechos"],
             "Entrada": t["entrada"], "Em cache": t["cache"], "Saída": t["saida"],
             "Total gasto": brl(t["brl"], 5), "Tempo": f"{t['duracaoMs'] / 1000:.1f}s".replace(".", ",")}
            for t in turnos
        ]), hide_index=True, width="stretch", height=360, column_config={
            "Quando": st.column_config.TextColumn(width="medium"),
            "Pergunta": st.column_config.TextColumn(width="large"),
        })
    else:
        st.caption("Nenhuma mensagem no período.")

with st.expander("Chamada a chamada (livro-caixa)", icon=":material/receipt_long:"):
    if chamadas:
        st.dataframe(pd.DataFrame([
            {"Quando": data_hora(c["quando"]), "Protocolo": c.get("protocolo") or "—", "Operação": c["rotuloOperacao"],
             "Modelo": c["modelo"], "Entrada": c["entrada"], "Em cache": c["cache"], "Saída": c["saida"],
             "R$ entrada": brl(c["brlEntrada"], 6), "R$ saída": brl(c["brlSaida"], 6), "R$ total": brl(c["brl"], 6),
             "Medido pela API": c["medidoPelaApi"], "Pergunta": c["pergunta"]}
            for c in chamadas
        ]), hide_index=True, width="stretch", column_config={"Quando": st.column_config.TextColumn(width="medium")})

# --- configuração e prompts ------------------------------------------------
if config:
    with st.expander("Prompts fixos e tabela de preços", icon=":material/tune:"):
        rag = config["rag"]
        st.caption(f"RAG: top-k {rag['topK']} · similaridade mínima {rag['similaridadeMinima']} · "
                   f"cache semântico ≥ {rag['similaridadeCache']}")
        st.dataframe(pd.DataFrame([
            {"Prompt fixo": p["nome"], "Modelo": p["modelo"], "Tokens": p["tokens"],
             "Entra no cache (≥ 1.024)": "sim" if p["elegivelCache"] else "não",
             "Sem cache": brl(p["brlCheio"], 6), "Com cache": brl(p["brlEmCache"], 6)}
            for p in config["prompts"]
        ]), hide_index=True, width="stretch")
        st.caption("O prompt fixo viaja em toda mensagem. Acima de 1.024 tokens de prefixo idêntico, a OpenAI cobra "
                   "25% do preço quando o cache pega. O roteador afinado (fine-tuning) dispensa o prompt longo.")
        st.markdown("**US\\$ por milhão de tokens**")
        st.dataframe(pd.DataFrame([
            {"Modelo": nome, "Entrada": p["entrada"], "Entrada em cache": p["cache"], "Saída": p["saida"]}
            for nome, p in config["tabela"].items()
        ]), hide_index=True, width="stretch")

with st.expander("Estimar o custo de um texto como entrada", icon=":material/calculate:"):
    texto = st.text_area("Texto (ex.: um prompt novo)", height=140)
    if texto:
        estimativa = api.post("/api/custos/estimar", {"texto": texto})
        st.markdown(md(f"**{inteiro(estimativa['tokens'])} tokens** em `{estimativa['modelo']}` = "
                       f"**{brl(estimativa['brl'], 6)}** ({usd(estimativa['usd'])}) a cada envio, sem cache."))
