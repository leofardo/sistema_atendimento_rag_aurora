"""Identidade visual e peças compartilhadas: tema escuro no estilo do Financeiro.

As cores base vêm do .streamlit/config.toml; aqui ficam o CSS dos detalhes que
o tema não cobre, o menu lateral (com o histórico de conversas) e a linha de
custo que aparece sob cada resposta.
"""

import streamlit as st

import api
from formatos import brl, data_hora, h, inteiro, md, usd

CSS = """
<style>
:root {
  --fundo: #0B1120; --card: #111A2C; --card-2: #16213A; --borda: #1E293B;
  --texto: #E2E8F0; --apagado: #8A99B4; --azul: #3B82F6; --azul-claro: #60A5FA;
  --verde: #22C55E; --vermelho: #EF4444; --amarelo: #F59E0B; --roxo: #A855F7;
}

/* Cabeçalho do Streamlit: transparente e sem a borda que cortava o título ao rolar */
header[data-testid="stHeader"] { background: transparent !important; box-shadow: none !important;
  border: none !important; }
[data-testid="stDecoration"] { display: none; }
/* Conversa e caixa de texto com a MESMA largura e o mesmo recuo */
.block-container { padding: 2.6rem 4rem 1rem 4rem; max-width: 1240px; }
[data-testid="stBottomBlockContainer"] { padding-left: 4rem; padding-right: 4rem; max-width: 1240px;
  margin-left: auto; margin-right: auto; }

/* Menu lateral alinhado ao título da página: o botão « sai do fluxo e não empurra a marca para baixo */
[data-testid="stSidebarHeader"] { height: 0 !important; min-height: 0 !important; padding: 0 !important; }
[data-testid="stSidebarCollapseButton"] { position: absolute; top: 1.4rem; right: .75rem; z-index: 10; }
[data-testid="stSidebarUserContent"] { padding-top: 1.65rem !important; }
[data-testid="stSidebarContent"] { overflow-x: hidden; }

/* Cabeçalho das páginas */
.sobretitulo { color: var(--apagado); font-size: .72rem; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; line-height: 1.2; }
.titulo-pagina { color: var(--texto); font-size: 1.85rem; font-weight: 800; line-height: 1.2; margin: .15rem 0 0 0; }
.subtitulo { color: var(--apagado); font-size: .86rem; margin-top: .3rem; }

/* Marca no menu lateral */
.marca { padding: 0 .25rem 1rem .25rem; }
.marca .rotulo { color: var(--apagado); font-size: .72rem; font-weight: 700; letter-spacing: .08em; line-height: 1.2; }
.marca .nome { color: var(--texto); font-size: 1.15rem; font-weight: 800; margin-top: .15rem; line-height: 1.2; }
.marca .descricao { color: var(--azul-claro); font-size: .8rem; font-weight: 600; margin-top: .2rem; }
[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"] { border-radius: .7rem; padding: .5rem .7rem; }
[data-testid="stSidebar"] a[data-testid="stPageLink-NavLink"]:hover { background: var(--card-2); }
.secao-lateral { color: var(--apagado); font-size: .7rem; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; margin: .4rem .25rem .3rem .25rem; }

/* Histórico de conversas: botões em lista, à esquerda, uma linha cada */
[class*="st-key-conversa-"] button { justify-content: flex-start; text-align: left; border: 1px solid transparent;
  padding: .35rem .6rem; min-height: 2.2rem; width: 100%; }
[class*="st-key-conversa-"] button > div, [class*="st-key-conversa-"] button [data-testid="stMarkdownContainer"] {
  justify-content: flex-start; text-align: left; width: 100%; }
[class*="st-key-conversa-"] button p { white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-size: .84rem; color: #CBD5E1; }
[class*="st-key-conversa-"] button:hover { background: var(--card-2); }
[class*="st-key-apagar-"] button { color: #64748B; min-height: 2.2rem; padding: .2rem; }
[class*="st-key-apagar-"] button:hover { color: var(--vermelho); }

/* Cards (st.metric com borda e containers com borda) */
[data-testid="stMetric"] { background: var(--card); padding: 1rem 1.15rem; }
[data-testid="stMetricLabel"] p { color: var(--apagado); font-size: .8rem; font-weight: 600; }
[data-testid="stVerticalBlockBorderWrapper"] { background: var(--card); }
.titulo-card { color: var(--texto); font-weight: 700; font-size: .98rem; }
.legenda-card { color: var(--apagado); font-size: .78rem; margin-top: .1rem; }

/* Chat */
[data-testid="stChatMessage"] { background: transparent; padding: .85rem 1rem; border-radius: 1rem; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  background: var(--card); border: 1px solid var(--borda); }

/* Linha de custo sob a resposta: texto pequeno, a conta entrada + saída = total */
.custo { color: var(--apagado); font-size: .76rem; line-height: 1.7; margin-top: .45rem; }
.custo b { color: #CBD5E1; font-weight: 600; }
.custo .total { color: var(--azul-claro); font-weight: 800; }
.custo .sinal { color: #475569; margin: 0 .35rem; }
.custo .rota-rag { color: var(--azul-claro); font-weight: 700; }
.custo .rota-cache, .custo .rota-regra { color: var(--verde); font-weight: 700; }
.custo .rota-humano { color: var(--amarelo); font-weight: 700; }
.custo .rota-erro, .custo .rota-nao_encontrado { color: var(--vermelho); font-weight: 700; }
.custo .rota-sem_base { color: var(--roxo); font-weight: 700; }

/* Barra de status (tela de custos) */
.status { display: flex; flex-wrap: wrap; gap: .3rem 1.1rem; align-items: center; color: var(--apagado);
  font-size: .8rem; border: 1px solid var(--borda); background: var(--card); border-radius: .85rem; padding: .7rem 1rem; }
.status b { color: var(--texto); font-weight: 600; }
.ponto { display: inline-block; width: .5rem; height: .5rem; border-radius: 50%; margin-right: .35rem; }
.ponto.ok { background: var(--verde); box-shadow: 0 0 8px rgba(34,197,94,.6); }
.ponto.erro { background: var(--vermelho); }

/* Estado vazio do chat */
.boas-vindas { border: 1px solid var(--borda); background: linear-gradient(135deg, #111A2C 0%, #0F1A33 100%);
  border-radius: 1.1rem; padding: 1.4rem 1.5rem; margin: .5rem 0 1rem 0; }
.boas-vindas .titulo { font-size: 1.15rem; font-weight: 800; color: var(--texto); }
.boas-vindas .texto { color: var(--apagado); font-size: .88rem; margin-top: .3rem; }
</style>
"""

PAGINAS = {
    "chat": ("paginas/chat.py", "Atendimento", ":material/forum:"),
    "custos": ("paginas/custos.py", "Custos e tokens", ":material/payments:"),
    "base": ("paginas/base.py", "Base de conhecimento", ":material/menu_book:"),
}


def estilos() -> None:
    st.html(CSS)


@st.cache_data(ttl=300, show_spinner=False)
def configuracao() -> dict | None:
    """Modelos, cotação e memória — muda raramente, então fica 5 min em cache."""
    try:
        return api.get("/api/custos/configuracao")
    except api.ErroApi:
        return None


@st.cache_data(ttl=30, show_spinner=False)
def conversas() -> list[dict]:
    try:
        return api.get("/api/chat/conversas", limite=30)["conversas"]
    except api.ErroApi:
        return []


# --- sessão de atendimento ----------------------------------------------------
def nova_conversa() -> None:
    dados = api.post("/api/chat/sessao")
    st.session_state.sessao = dados["sessao"]
    st.session_state.protocolo = dados["protocolo"]
    st.session_state.mensagens = []


def abrir_conversa(sessao: str) -> None:
    """Carrega uma conversa antiga com custos, rota e fontes de cada resposta."""
    dados = api.get(f"/api/chat/sessao/{sessao}")
    st.session_state.sessao = sessao
    st.session_state.protocolo = dados["protocolo"]
    st.session_state.mensagens = [{"autor": m["autor"], "texto": m["texto"], **(m.get("meta") or {})}
                                  for m in dados["mensagens"]]


def garantir_sessao(no_endereco: bool = False) -> None:
    """Garante uma conversa aberta.

    Só a página de Atendimento põe a conversa no endereço (?conversa=<id>), para
    o F5 não perder a conversa. Custos e Base são gerais: não carregam parâmetro.
    """
    pedida = st.query_params.get("conversa") if no_endereco else None
    try:
        if pedida and pedida != st.session_state.get("sessao"):
            abrir_conversa(pedida)
        elif "sessao" not in st.session_state:
            nova_conversa()
    except api.ErroApi as erro:
        st.error(str(erro))
        st.stop()
    if no_endereco:
        st.query_params["conversa"] = st.session_state.sessao
    elif "conversa" in st.query_params:
        del st.query_params["conversa"]


# --- menu lateral ---------------------------------------------------------------
def barra_lateral(paginas: dict) -> None:
    with st.sidebar:
        st.html('<div class="marca"><div class="rotulo">ATENDIMENTO</div>'
                '<div class="nome">Aurora Casa &amp; Tech</div>'
                '<div class="descricao">Lia, assistente virtual</div></div>')
        for pagina in paginas.values():
            st.page_link(pagina)

        st.divider()
        if st.button("Nova conversa", icon=":material/add_comment:", width="stretch", type="primary"):
            try:
                nova_conversa()
                conversas.clear()
                st.switch_page(paginas["chat"])
            except api.ErroApi as erro:
                st.error(str(erro))

        lista = conversas()
        if lista:
            st.html('<div class="secao-lateral">Conversas</div>')
            atual = st.session_state.get("sessao")
            # Destaque da conversa aberta (o Streamlit põe a classe st-key-<key> no botão).
            st.html(f'<style>.st-key-conversa-{atual} button {{ background: rgba(59,130,246,.16) !important; '
                    f'border-color: rgba(59,130,246,.35) !important; }}</style>')
            for conversa in lista:
                titulo = conversa.get("titulo") or f"Conversa {conversa['protocolo'][-6:]}"
                ajuda = (f"{data_hora(conversa.get('atualizada_em'))} · Protocolo {conversa['protocolo']} · "
                         f"{conversa['mensagens']} mensagens · {brl(conversa['brl'], 5)}")
                nome, lixeira = st.columns([6, 1], gap=None, vertical_alignment="center")
                if nome.button(titulo, key=f"conversa-{conversa['sessao']}", width="stretch", type="tertiary",
                               help=ajuda.replace("$", "\\$")):
                    try:
                        abrir_conversa(conversa["sessao"])
                        st.switch_page(paginas["chat"])
                    except api.ErroApi as erro:
                        st.error(str(erro))
                if lixeira.button("", icon=":material/delete:", key=f"apagar-{conversa['sessao']}", type="tertiary",
                                  help="Excluir conversa"):
                    confirmar_exclusao(conversa["sessao"], titulo, paginas)


@st.dialog("Excluir conversa")
def confirmar_exclusao(sessao: str, titulo: str, paginas: dict) -> None:
    st.markdown(md(f"Excluir **{titulo}**?"))
    st.caption("As mensagens somem do histórico. Os custos continuam na tela de Custos, porque foram gastos de verdade.")
    cancelar, excluir = st.columns(2)
    if cancelar.button("Cancelar", width="stretch"):
        st.rerun()
    if excluir.button("Excluir", type="primary", icon=":material/delete:", width="stretch"):
        api.delete(f"/api/chat/sessao/{sessao}")
        conversas.clear()
        if st.session_state.get("sessao") == sessao:
            nova_conversa()
        st.rerun()


# --- blocos -------------------------------------------------------------------------
def cabecalho(sobretitulo: str, titulo: str, subtitulo: str = "") -> None:
    st.html(
        f'<div class="sobretitulo">{h(sobretitulo)}</div><div class="titulo-pagina">{h(titulo)}</div>'
        + (f'<div class="subtitulo">{h(subtitulo)}</div>' if subtitulo else "")
    )


def titulo_card(titulo: str, legenda: str = "") -> None:
    st.html(f'<div class="titulo-card">{h(titulo)}</div>'
            + (f'<div class="legenda-card">{h(legenda)}</div>' if legenda else ""))


def linha_de_custo(mensagem: dict) -> str:
    """Duas linhas de texto pequeno sob a resposta.

    1) rota · intenção · tempo   2) Entrada ... + Saída ... = Total gasto.
    As parcelas são arredondadas em 5 casas e o total é a soma DELAS, para a
    conta fechar na tela (antes R$ 0,0007 + R$ 0,0001 aparecia como R$ 0,0009).
    """
    rota = mensagem.get("rota") or "erro"
    partes = [f'Rota: <span class="rota-{h(rota)}">{h(mensagem.get("rotuloRota") or rota)}</span>']
    if mensagem.get("intencao"):
        partes.append(f'Intenção: <b>{h(mensagem["intencao"])}</b>')
    tempo = f"Tempo: <b>{mensagem.get('duracaoMs', 0) / 1000:.1f}s</b>"
    primeiro = mensagem.get("primeiroTextoMs")
    if primeiro and mensagem.get("duracaoMs", 0) - primeiro > 300:
        # A resposta aparece em streaming: a espera real é até a primeira palavra.
        tempo += f" (texto em {primeiro / 1000:.1f}s)"
    partes.append(tempo.replace(".", ","))
    primeira = '<span class="sinal">|</span>'.join(partes)

    custos = mensagem.get("custos") or []
    if not custos:
        return f'<div class="custo">{primeira}<br><b>Sem custo</b>: respondida sem chamar a IA.</div>'

    entrada = round(sum(c["brlEntrada"] for c in custos), 5)
    saida = round(sum(c["brlSaida"] for c in custos), 5)
    tokens_entrada = sum(c["entrada"] for c in custos)
    tokens_saida = sum(c["saida"] for c in custos)
    cache = sum(c["cache"] for c in custos)
    detalhe = " · ".join(f"{c['rotuloOperacao']} {brl(c['brl'], 5)} ({c['modelo']})" for c in custos)
    dolar = usd(sum(c["usd"] for c in custos))
    em_cache = f" ({inteiro(cache)} em cache)" if cache else ""
    sinal = '<span class="sinal">{}</span>'
    segunda = (
        f'Entrada <b>{inteiro(tokens_entrada)} tokens</b>{em_cache} {h(brl(entrada, 5))}'
        f'{sinal.format("+")}Saída <b>{inteiro(tokens_saida)} tokens</b> {h(brl(saida, 5))}'
        f'{sinal.format("=")}Total gasto <span class="total">{h(brl(entrada + saida, 5))}</span>'
    )
    if all(c.get("operacao") == "roteamento" for c in custos):
        # Resposta pronta depois do roteador: o texto não custou nada, só entender a pergunta.
        segunda += ' <span class="sinal">·</span>só o roteador, para entender a pergunta; o texto não usou IA'
    return f'<div class="custo" title="{h(detalhe)} · {h(dolar)}">{primeira}<br>{segunda}</div>'


def barra_de_status(config: dict | None) -> None:
    if not config:
        st.html('<div class="status"><span><span class="ponto erro"></span><b>API fora do ar</b></span>'
                '<span>Suba o backend: uvicorn app.main:app (dentro de backend/)</span></div>')
        return
    modelos = config["modelos"]
    st.html(
        '<div class="status">'
        '<span><span class="ponto ok"></span><b>API online</b></span>'
        f'<span>Resposta <b>{h(modelos["resposta"])}</b></span>'
        f'<span>Roteador <b>{h(modelos["roteador"])}</b></span>'
        f'<span>Embedding <b>{h(modelos["embedding"])}</b></span>'
        f'<span>Memória <b>{h(config["conversa"]["memoria"])}</b></span>'
        '</div>'
    )
