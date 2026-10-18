"""Base de conhecimento: o que está indexado e como a busca enxerga cada pergunta."""

from datetime import datetime

import pandas as pd
import streamlit as st

import api
import ui
from formatos import BRASILIA, brl, h, inteiro, md


@st.cache_data(ttl=60, show_spinner=False)
def documentos() -> dict:
    return api.get("/api/base/documentos")


@st.cache_data(ttl=300, show_spinner=False)
def trechos_da_fonte(fonte: str) -> list[dict]:
    return api.get("/api/base/trechos", fonte=fonte)["trechos"]


@st.cache_data(ttl=300, show_spinner=False)
def pedidos() -> list[dict]:
    return api.get("/api/base/pedidos")["pedidos"]


ui.cabecalho("Conhecimento", "Base de conhecimento",
             "O que está indexado no RAG e como a busca híbrida enxerga cada pergunta.")
try:
    dados = documentos()
except api.ErroApi as erro:
    st.error(str(erro))
    st.stop()

st.write("")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Documentos", len(dados["documentos"]), border=True)
c2.metric("Trechos indexados", inteiro(dados["totalTrechos"]), border=True)
c3.metric("Tokens na base", inteiro(sum(d["tokens"] for d in dados["documentos"])), border=True)
c4.metric("Respostas no cache semântico", inteiro(dados["cacheRespostas"]), border=True)

with st.container(border=True):
    topo, acao1, acao2 = st.columns([3, 1, 1], vertical_alignment="center")
    topo.markdown("**Documentos**")
    if acao1.button("Reindexar", icon=":material/sync:", width="stretch", help="Só trechos novos ou alterados pagam embedding."):
        with st.spinner("Indexando…"):
            try:
                resultado = api.post("/api/base/reindexar")
                custo = resultado.get("custo")
                st.success(md(
                    f"{resultado['trechos']} trechos · {resultado['novos']} novos · {resultado['reaproveitados']} "
                    f"reaproveitados · {resultado['removidos']} removidos · {inteiro(resultado['tokensEmbedding'])} tokens · "
                    f"{brl(custo['brl'], 6) if custo else 'custo zero'}"))
                documentos.clear()
                trechos_da_fonte.clear()
            except api.ErroApi as erro:
                st.error(str(erro))
    if acao2.button("Limpar cache", icon=":material/delete_sweep:", width="stretch", help="Apaga as respostas do cache semântico."):
        st.info(f"{api.post('/api/base/cache/limpar')['removidos']} respostas removidas do cache.")
        documentos.clear()
    st.dataframe(pd.DataFrame([
        {"Documento": d["titulo"], "Fonte": d["fonte"], "Tipo": d["tipo"], "Trechos": d["trechos"],
         "Tokens": d["tokens"], "Origem": d["url"]}
        for d in dados["documentos"]
    ]), hide_index=True, width="stretch", column_config={"Origem": st.column_config.LinkColumn("Origem")})
    if dados["totalTrechos"] == 0:
        st.warning(md("A base está vazia: clique em **Reindexar** (custa cerca de R$ 0,003)."))

with st.container(border=True):
    ui.titulo_card("Respostas em cache",
                   "Perguntas já respondidas que são reaproveitadas quando alguém pergunta algo quase igual "
                   "(similaridade ≥ 0,95). Somem sozinhas em 7 dias ou quando a base muda.")
    try:
        itens = api.get("/api/base/cache")["itens"]
    except api.ErroApi as erro:
        itens = []
        st.error(str(erro))
    if not itens:
        st.caption("Nenhuma resposta em cache. Elas entram quando a primeira pergunta de uma conversa passa pelo RAG.")
    else:
        selecao = st.dataframe(pd.DataFrame([
            {"Pergunta": i["pergunta"], "Resposta": i["resposta"],
             "Guardada em": datetime.fromtimestamp(i["criadoEm"], BRASILIA).strftime("%d/%m/%Y %H:%M:%S"),
             "Expira em": f"{i['expiraEmHoras'] / 24:.1f} dias".replace(".", ","),
             "Fontes": " · ".join(f"{f['fonte']} {f['referencia']}" for f in i["fontes"])}
            for i in itens
        ]), hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="tabela-cache",
            column_config={"Pergunta": st.column_config.TextColumn(width="medium"),
                           "Resposta": st.column_config.TextColumn(width="large")})
        escolhidas = selecao.selection.rows
        if st.button(f"Apagar selecionadas ({len(escolhidas)})", icon=":material/delete:",
                     disabled=not escolhidas, help="Marque as linhas na tabela"):
            for linha in escolhidas:
                api.delete(f"/api/base/cache/{itens[linha]['id']}")
            documentos.clear()
            st.rerun()

with st.container(border=True):
    st.markdown("**Testar a busca**")
    st.caption("Vetorial + BM25 unidas por RRF, mais a consulta direta quando a frase cita um artigo. "
               "Custa só o embedding da consulta.")
    consulta = st.text_input("Consulta", placeholder="ex.: prazo para desistir de compra feita pela internet",
                             label_visibility="collapsed")
    if consulta:
        try:
            busca = api.get("/api/base/buscar", q=consulta, k=6)
        except api.ErroApi as erro:
            st.error(str(erro))
        else:
            st.html(ui.pills((f"embedding {inteiro(busca['custo']['entrada'])} tokens", ""),
                             (brl(busca["custo"]["brl"], 6), "valor")))
            if not busca["resultados"]:
                st.info("Nenhum trecho acima da similaridade mínima: o atendimento responderia que não encontrou.")
            for n, r in enumerate(busca["resultados"], start=1):
                posicoes = [f"vetorial #{r['posicaoVetorial']}" if r["posicaoVetorial"] else "",
                            f"BM25 #{r['posicaoBm25']}" if r["posicaoBm25"] else ""]
                with st.container(border=True):
                    st.html(f"<b>{n}. {h(r['caminho'])}</b>"
                            + ui.pills((f"similaridade {r['similaridade']:.3f}", "valor"),
                                       *[(p, "") for p in posicoes if p]))
                    st.text(r["texto"])

with st.container(border=True):
    st.markdown("**Trechos por fonte**")
    fonte = st.selectbox("Fonte", sorted({d["fonte"] for d in dados["documentos"]}), label_visibility="collapsed")
    if fonte:
        for trecho in trechos_da_fonte(fonte):
            with st.expander(f"{trecho['referencia']} · {trecho.get('tokens', 0)} tokens"):
                st.caption(trecho["caminho"])
                st.text(trecho["texto"])

with st.expander("Pedidos fictícios (para testar o atendimento)", icon=":material/inventory_2:"):
    st.dataframe(pd.DataFrame(pedidos()), hide_index=True, width="stretch")
