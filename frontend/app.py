"""Frontend Streamlit do atendimento da Aurora.

Rodar (de dentro de frontend/):  streamlit run app.py
"""

import streamlit as st

import ui

st.set_page_config(page_title="Atendimento Aurora", page_icon=":material/support_agent:", layout="wide",
                   initial_sidebar_state="expanded")
ui.estilos()

paginas = {
    chave: st.Page(arquivo, title=titulo, icon=icone, default=chave == "chat",
                   url_path=None if chave == "chat" else chave)
    for chave, (arquivo, titulo, icone) in ui.PAGINAS.items()
}
# Menu desenhado à mão (marca, links, nova conversa e histórico), como no Financeiro.
navegacao = st.navigation(list(paginas.values()), position="hidden")
# A página de Atendimento lê ?conversa= do endereço; as outras o removem.
ui.garantir_sessao(no_endereco=navegacao.url_path == "")
ui.barra_lateral(paginas)
navegacao.run()
