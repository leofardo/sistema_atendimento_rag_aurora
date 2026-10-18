"""Baixa as leis federais de atendimento do Planalto e grava em Markdown limpo.

Uso (de dentro de backend/):  python -m scripts.baixar_documentos

Por que limpar antes de indexar: o texto compilado do Planalto traz tudo que
já foi revogado (riscado em <strike>) e anotações como "(Redação dada pela Lei
nº 14.181, de 2021)" em quase todo artigo. Nada disso ajuda a responder um
cliente, mas tudo vira token — no embedding, uma vez, e no prompt, em TODA
pergunta que recuperar aquele trecho. Limpar aqui é economia que se paga para
sempre.

O Markdown de saída preserva a estrutura (## capítulo, ### seção, uma linha por
artigo). É ela que o chunker (app.rag.documentos) usa para cortar por artigo.
"""

import re
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

PASTA = Path(__file__).resolve().parents[1] / "dados" / "documentos"
CABECALHOS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"}

# (arquivo, sigla, título, url, último artigo a manter). O CDC vai só até o
# art. 60 (Título I, os direitos do consumidor): o resto é crime, processo
# judicial e organização do SNDC — assunto que o atendimento não responde e
# que só disputaria espaço na busca com os artigos que importam.
LEIS = [
    ("lei-8078-cdc.md", "CDC", "Código de Defesa do Consumidor (Lei nº 8.078/1990)",
     "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm", 60),
    ("decreto-11034-sac.md", "Decreto SAC", "Decreto nº 11.034/2022 (regras do SAC)",
     "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2022/decreto/d11034.htm", None),
    ("decreto-7962-comercio-eletronico.md", "Decreto Comércio Eletrônico",
     "Decreto nº 7.962/2013 (comércio eletrônico)",
     "https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/decreto/d7962.htm", None),
    ("lei-10048-atendimento-prioritario.md", "Lei Atendimento Prioritário",
     "Lei nº 10.048/2000 (atendimento prioritário)",
     "https://www.planalto.gov.br/ccivil_03/leis/l10048.htm", None),
]

ANOTACOES = re.compile(
    r"\((?:Redação dada|Incluíd[oa]|Acrescentad[oa]|Vide|Revogad[oa]|Vigência|Regulamento|Promulgação|"
    r"Renumerad[oa]|Produção de efeito|Vetado|VETADO)[^()]*(?:\([^()]*\)[^()]*)*\)",
    re.IGNORECASE,
)
ESTRUTURA = re.compile(r"^(LIVRO|TÍTULO|CAPÍTULO|Seção|SEÇÃO|Subseção)\b", re.IGNORECASE)
ARTIGO = re.compile(r"^Art\.\s*(\d+)")
SO_ROTULO = re.compile(r"^(LIVRO|TÍTULO|CAPÍTULO|Seção|SEÇÃO|Subseção)\s+[IVXLC]+(-[A-Z])?$", re.IGNORECASE)
VAZIO = re.compile(r"(Art\.\s*\d+|§\s*\d+|Parágrafo único|[IVXLC]+\s*[-–])\s*[ºo°]?\s*[.\-–]*\s*")


def _limpar(linha: str) -> str:
    linha = ANOTACOES.sub("", linha)
    linha = re.sub(r"\s+", " ", linha).strip()
    linha = re.sub(r"(\d)\s+o(?=[\s.,;:)]|$)", r"\1º", linha)  # "art. 1 o" -> "art. 1º"
    linha = re.sub(r"\s+([.,;:])", r"\1", linha)
    return re.sub(r"\.{2,}$", ".", linha)


def _paragrafos(html: bytes) -> list[str]:
    sopa = BeautifulSoup(html, "html.parser", from_encoding="windows-1252")
    for riscado in sopa.find_all(["strike", "s", "del"]):
        riscado.decompose()
    linhas = [_limpar(p.get_text(" ")) for p in sopa.find_all("p")]
    return [linha for linha in linhas if linha]


def _para_markdown(paragrafos: list[str], titulo: str, sigla: str, url: str, ultimo_artigo: int | None) -> str:
    saida = ["---", f"titulo: {titulo}", f"fonte: {sigla}", f"url: {url}", "tipo: lei", "---", "", f"# {titulo}", ""]
    comecou = False
    pendente_estrutura: str | None = None

    for linha in paragrafos:
        artigo = ARTIGO.match(linha)
        if artigo:
            comecou = True
            if ultimo_artigo and int(artigo.group(1)) > ultimo_artigo:
                break
        if not comecou and not ESTRUTURA.match(linha):
            continue  # ementa, preâmbulo, "O PRESIDENTE DA REPÚBLICA..."
        comecou = True

        # Assinaturas e notas do rodapé: fim do texto útil.
        if linha.startswith(("Brasília,", "Este texto não substitui")):
            break

        if ESTRUTURA.match(linha):
            if pendente_estrutura:
                saida += ["", pendente_estrutura, ""]
                pendente_estrutura = None
            nivel = "###" if linha.lower().startswith(("seção", "subseção")) else "##"
            if SO_ROTULO.match(linha):
                pendente_estrutura = f"{nivel} {linha}"  # o nome vem no parágrafo seguinte
            else:
                saida += ["", f"{nivel} {linha}", ""]
            continue
        if pendente_estrutura:
            # "CAPÍTULO VI" vem num parágrafo e "Da Proteção Contratual" no seguinte.
            if not ARTIGO.match(linha) and len(linha) < 120 and not linha.startswith(("§", "Parágrafo")):
                saida += ["", f"{pendente_estrutura} — {linha}", ""]
                pendente_estrutura = None
                continue
            saida += ["", pendente_estrutura, ""]
            pendente_estrutura = None

        if VAZIO.fullmatch(linha):
            continue  # dispositivo revogado ou vetado: só sobrou o número
        saida.append(linha)

    # O corte no último artigo deixa para trás o cabeçalho do título seguinte.
    while saida and (not saida[-1].strip() or saida[-1].startswith("#")):
        saida.pop()
    return "\n".join(saida).strip() + "\n"


def main() -> int:
    PASTA.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=CABECALHOS, timeout=60, follow_redirects=True) as cliente:
        for arquivo, sigla, titulo, url, ultimo in LEIS:
            resposta = cliente.get(url)
            resposta.raise_for_status()
            texto = _para_markdown(_paragrafos(resposta.content), titulo, sigla, url, ultimo)
            (PASTA / arquivo).write_text(texto, encoding="utf-8")
            print(f"{arquivo}: {len(texto):,} caracteres")
    return 0


if __name__ == "__main__":
    sys.exit(main())
