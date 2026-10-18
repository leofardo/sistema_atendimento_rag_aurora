"""Chat de atendimento com o custo de cada resposta logo abaixo dela.

Sob cada resposta, em texto pequeno: a rota que respondeu e a conta
Entrada + Saída = Total gasto. Entrada é tudo que foi enviado aos modelos
(instrução + histórico + trechos + pergunta); saída é o que eles escreveram —
cerca de 4x mais cara por token. O custo da conversa inteira fica no botão
"Atendimento", no canto direito.
"""

import re
import time

import pandas as pd
import streamlit as st

import api
import ui
from formatos import brl, h, inteiro, md, porcentagem, usd

PAUSA_DIGITACAO = 0.02  # segundos por palavra (~50 palavras/s): rápido, mas dá para ver escrevendo
# Genéricas de propósito: cada cliente tem o seu pedido e o seu produto.
SUGESTOES = [
    "Quero devolver uma compra feita pelo site",
    "Meu produto chegou com defeito, o que eu faço?",
    "Como cancelo um pedido?",
    "Em quanto tempo cai o reembolso?",
    "Qual o prazo de entrega e quando o frete é grátis?",
    "Quero falar com um atendente",
]

ui.garantir_sessao(no_endereco=True)
mensagens: list[dict] = st.session_state.mensagens
respostas = [m for m in mensagens if m["autor"] == "assistente"]


# --- custo da conversa (janela) ------------------------------------------------
@st.dialog("Custo desta conversa", width="large")
def custo_da_conversa() -> None:
    custos = [c for m in respostas for c in m.get("custos") or []]
    entrada = sum(c["entrada"] for c in custos)
    cache = sum(c["cache"] for c in custos)
    gratis = sum(1 for m in respostas if not m.get("custos"))
    c1, c2, c3 = st.columns(3)
    c1.metric("Total gasto", brl(sum(c["brl"] for c in custos), 5), help=usd(sum(c["usd"] for c in custos)), border=True)
    c2.metric("Respostas", len(respostas), border=True)
    c3.metric("Sem chamar a IA", f"{gratis} de {len(respostas)}", border=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Tokens de entrada", inteiro(entrada), border=True)
    c2.metric(f"Em cache · {porcentagem(cache, entrada)}", inteiro(cache), border=True)
    c3.metric("Tokens de saída", inteiro(sum(c["saida"] for c in custos)), border=True)
    if not respostas:
        st.caption("Ainda não há mensagens nesta conversa.")
        return
    perguntas = [m["texto"] for m in mensagens if m["autor"] == "cliente"]
    st.dataframe(pd.DataFrame([
        {"Pergunta": pergunta, "Rota": r.get("rotuloRota"),
         "Entrada": sum(c["entrada"] for c in r.get("custos") or []),
         "Saída": sum(c["saida"] for c in r.get("custos") or []), "Total gasto": brl(r.get("totalBrl", 0.0), 5)}
        for pergunta, r in zip(perguntas, respostas)
    ]), hide_index=True, width="stretch",
        column_config={"Pergunta": st.column_config.TextColumn(width="large")})


# --- cabeçalho ----------------------------------------------------------------
esquerda, direita = st.columns([3, 1], vertical_alignment="center")
with esquerda:
    pass  # sem título: o menu lateral já diz quem é a Lia
with direita, st.container(horizontal=True, horizontal_alignment="right"):
    with st.popover(f"Protocolo {st.session_state.protocolo}", icon=":material/info:"):
        st.html(f'<div class="sobretitulo">Protocolo</div><div class="titulo-pagina" style="font-size:1.3rem">'
                f'{h(st.session_state.protocolo)}</div>')
        st.caption("Informe este número em qualquer canal: o atendente vê todo o histórico.")
        st.caption(f"{len(respostas)} respostas nesta conversa")
        if st.button("Ver custo da conversa", icon=":material/payments:", width="stretch", type="primary"):
            custo_da_conversa()

st.write("")


# --- mensagens ---------------------------------------------------------------
def _fontes(mensagem: dict) -> None:
    cache = mensagem.get("cache")
    if cache:
        st.caption(md(f"Reaproveitada do cache semântico: “{cache['perguntaOriginal'][:90]}” "
                      f"(similaridade {cache['similaridade']:.3f})"))
    fontes = mensagem.get("fontes") or []
    if not fontes:
        return
    with st.expander(f"Fontes consultadas ({len(fontes)})", icon=":material/menu_book:"):
        for n, fonte in enumerate(fontes, start=1):
            posicoes = " · ".join(p for p in (
                f"vetorial #{fonte['posicaoVetorial']}" if fonte.get("posicaoVetorial") else "",
                f"BM25 #{fonte['posicaoBm25']}" if fonte.get("posicaoBm25") else "") if p)
            st.html(f'<b>[{n}] {h(fonte["caminho"])}</b><div class="custo">similaridade '
                    f'{fonte["similaridade"]:.3f} · {h(posicoes)}</div>')
            st.text(fonte["texto"][:700] + ("…" if len(fonte["texto"]) > 700 else ""))


for mensagem in mensagens:
    with st.chat_message("user" if mensagem["autor"] == "cliente" else "assistant"):
        st.markdown(md(mensagem["texto"]))
        if mensagem["autor"] == "assistente":
            st.html(ui.linha_de_custo(mensagem))
            _fontes(mensagem)

pergunta = st.chat_input("Escreva sua mensagem…")

if not mensagens and not pergunta:
    st.html('<div class="boas-vindas"><div class="titulo">Olá! Sou a Lia, assistente virtual da Aurora.</div>'
            '<div class="texto">Pergunte sobre pedidos, trocas, devoluções, garantia, pagamento, entrega ou seus '
            'direitos como consumidor.</div></div>')
    colunas = st.columns(3)
    for n, sugestao in enumerate(SUGESTOES):
        if colunas[n % 3].button(sugestao, key=f"sug-{n}", width="stretch"):
            pergunta = sugestao

if pergunta:
    mensagens.append({"autor": "cliente", "texto": pergunta})
    with st.chat_message("user"):
        st.markdown(md(pergunta))

    final: dict = {}
    erros: list[str] = []
    brutos: list[str] = []

    def texto_em_streaming():
        for evento in api.conversar(st.session_state.sessao, pergunta):
            if evento["tipo"] == "delta":
                brutos.append(evento["texto"])
                # Efeito de digitação, palavra a palavra. Só visual: o custo é por
                # token gerado, e o texto já chegou. Resposta pronta e do cache
                # chegam inteiras, e sem isto apareceriam de uma vez.
                for palavra in re.findall(r"\S+\s*|\s+", evento["texto"]):
                    yield md(palavra)  # "R$ 299,00" não pode virar fórmula
                    time.sleep(PAUSA_DIGITACAO)
            elif evento["tipo"] == "erro":
                erros.append(evento["mensagem"])
            elif evento["tipo"] == "fim":
                final.update(evento)

    with st.chat_message("assistant"):
        st.write_stream(texto_em_streaming())
        for erro in erros:
            st.error(erro)

    mensagens.append({
        "autor": "assistente",
        "texto": "".join(brutos) or ("⚠️ " + "; ".join(erros) if erros else ""),
        "custos": final.get("custos", []), "rota": final.get("rota"), "rotuloRota": final.get("rotuloRota"),
        "intencao": final.get("intencao"), "fontes": final.get("fontes", []), "cache": final.get("cache"),
        "totalBrl": final.get("totalBrl", 0.0), "duracaoMs": final.get("duracaoMs", 0),
    })
    ui.conversas.clear()  # a conversa nova (ou atualizada) aparece no menu lateral
    st.rerun()
