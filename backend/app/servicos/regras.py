"""Respostas prontas: o que dá para responder sem chamar modelo nenhum.

Custo do turno: zero. Saudação, "obrigado", horário, menu, "quero falar com
um atendente" e "cadê o pedido 100231" são uma fatia grande de qualquer
atendimento e têm sempre a mesma resposta — pagar um modelo para isso é
desperdício (dica 7).

A regra é CONSERVADORA de propósito: só casa mensagem curta e inequívoca.
"Oi, meu produto chegou quebrado" não é saudação; vai para o modelo.
"""

import re
import unicodedata
from datetime import date

from app.repositorios import pedidos

MENU = (
    "Posso ajudar com:\n"
    "- **Pedidos**: status, rastreio, atraso (me diga o número do pedido)\n"
    "- **Trocas e devoluções**: arrependimento, troca, produto com defeito\n"
    "- **Cancelamento e reembolso**\n"
    "- **Pagamento, frete, cupons e garantia estendida**\n"
    "- **Seus direitos** como consumidor (CDC e regras do SAC)\n"
    "- **Falar com um atendente** humano"
)

FORA_DO_ESCOPO = (
    "Por aqui eu só consigo ajudar com compras na Aurora Casa & Tech (eletrodomésticos, eletrônicos, "
    "informática e utilidades para o lar): pedidos, trocas, devoluções, garantia, pagamento, entrega e "
    "seus direitos como consumidor. Posso ajudar com alguma dessas coisas?"
)

PEDIR_NUMERO_DO_PEDIDO = (
    "Claro! Me passa o número do pedido (são 6 dígitos, aparece em \"Meus pedidos\" no site ou no app e no "
    "e-mail de confirmação) que eu confiro o status, a previsão de entrega e o rastreio."
)

HORARIOS = (
    "Nossos canais:\n"
    "- **Assistente virtual** (aqui): 24h, todos os dias\n"
    "- **Atendente humano** no chat e WhatsApp (11) 4000-0000: seg. a sáb. 8h–22h; dom. e feriados 9h–18h\n"
    "- **SAC 0800 000 0000** (gratuito): 24h, com atendente humano das 8h às 22h\n"
    "- **E-mail** atendimento@aurora.exemplo: resposta em até 2 dias úteis\n"
    "- **Lojas**: seg. a sáb. 10h–22h; dom. e feriados 14h–20h"
)


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", texto)).strip()


SAUDACAO = re.compile(r"^(oi+|ola|ole|opa|hey|eai|e ai|bom dia|boa tarde|boa noite|tudo bem|tudo bom)( (tudo bem|tudo bom|lia|aurora|td bem))*$")
AGRADECIMENTO = re.compile(r"^((ok|certo|beleza|blz|perfeito|entendi|show|otimo) )?(muito )?(obrigad[oa]|obg|valeu|vlw|brigad[oa]|agradeco)( (pela ajuda|lia|mesmo|demais))*$")
HUMANO = re.compile(r"^(quero |queria |gostaria de |preciso |posso )?(falar|conversar|ser atendid[oa]) (com )?(um |uma |o |a )?(atendente|humano|pessoa|alguem|operador|operadora)( (por favor|pf|pfv|agora))?$|^(atendente|humano)( (por favor|pf|pfv))?$")
MENU_PEDIDO = re.compile(r"^(menu|opcoes|ajuda|help|o que voce faz|como voce pode me ajudar)$")
HORARIO = re.compile(r"^(qual (e )?o )?(horario|horarios|telefone|whatsapp|zap|canais|contato|email)( (de|do|da|dos) (atendimento|sac|lojas?|voces))?$")
PEDIDO = re.compile(r"^(?:(?:status|rastrear|rastreio|rastreamento|cade|onde esta|acompanhar|situacao)(?: do| o| meu)*(?: pedido)?|(?:meu )?pedido)? ?(?:n |numero |no )?(\d{6})$")


NUMERO_DE_PEDIDO = re.compile(r"\b(\d{6})\b")
PALAVRAS_DE_STATUS = re.compile(r"status|situacao|cade|onde (esta|ta)|como (esta|ta)|rastre|acompanh|andamento|"
                                r"ja (saiu|foi enviado)|previsao")
# Pedido de AÇÃO sobre o pedido não é consulta de status: vai para o roteador.
PALAVRAS_DE_ACAO = re.compile(r"cancel|devol|troc|defeit|quebr|arrepend|estorn|reembols|garanti|atras|errad|"
                              r"nao (chegou|recebi)|cobr|reclam")


def _pergunta_de_status(texto: str) -> re.Match | None:
    """'como está o status do pedido 100245?' -> status direto do banco, sem roteador.

    O padrão PEDIDO é estrito; este pega a pergunta de status escrita de outro
    jeito. Sem palavra de ação ("cancelar", "atrasou"), que pede mais que o status.
    """
    if PALAVRAS_DE_STATUS.search(texto) and not PALAVRAS_DE_ACAO.search(texto):
        return NUMERO_DE_PEDIDO.search(texto)
    return None


def _continuacao_de_status(texto: str, intencao_anterior: str | None) -> re.Match | None:
    """'e o pedido 100250?' / 'o 100250' — mensagem curta, só com o número, seguindo um status.

    Só vale se a conversa estava em status (ou começa agora): depois de
    "quero cancelar", o número informado é para cancelar, e isso vai ao roteador.
    """
    if intencao_anterior not in (None, "status_pedido") or len(texto.split()) > 5:
        return None
    if PALAVRAS_DE_ACAO.search(texto):
        return None
    return NUMERO_DE_PEDIDO.search(texto)


def pede_status(mensagem: str) -> bool:
    """A mensagem ATUAL pede o status? (número do pedido ou palavra de status, sem pedir ação).

    O roteador puxa o número do pedido do histórico — certo para dar contexto,
    errado para decidir a rota: "como funciona o prazo?" logo depois de um
    status virava o mesmo status de novo (teste real).
    """
    texto = _normalizar(mensagem)
    if PALAVRAS_DE_ACAO.search(texto):
        return False
    return bool(NUMERO_DE_PEDIDO.search(texto) or PALAVRAS_DE_STATUS.search(texto))


def _nome_do_dia(hoje: date) -> str:
    return ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][hoje.weekday()]


def responder_por_regra(
    mensagem: str, protocolo: str, primeira_mensagem: bool, intencao_anterior: str | None = None
) -> tuple[str, str] | None:
    """(texto, intenção) quando dá para responder sem IA; None caso contrário.

    `intencao_anterior` é a intenção da última resposta: "e o pedido 100250?"
    depois de um status é outro status; depois de "quero cancelar", não é.
    """
    texto = _normalizar(mensagem)
    if len(texto) > 60:
        return None

    if SAUDACAO.match(texto):
        abertura = f"Olá! Sou a Lia, assistente virtual da Aurora Casa & Tech. Seu protocolo é **{protocolo}**.\n\n" \
            if primeira_mensagem else "Olá de novo! "
        return abertura + MENU, "saudacao"
    if AGRADECIMENTO.match(texto):
        return f"Eu que agradeço! Se precisar, é só chamar. Seu protocolo: **{protocolo}**.", "agradecimento"
    if HUMANO.match(texto):
        return transferencia_para_humano(protocolo), "falar_humano"
    if MENU_PEDIDO.match(texto):
        return MENU, "menu"
    if HORARIO.match(texto):
        return HORARIOS, "atendimento_sac"

    achado = PEDIDO.match(texto) or _pergunta_de_status(texto) or _continuacao_de_status(texto, intencao_anterior)
    if achado:
        pedido = pedidos.buscar(achado.group(1))
        if not pedido:
            return (f"Não encontrei o pedido **{achado.group(1)}**. Confere o número em \"Meus pedidos\" "
                    "no site ou no app (são 6 dígitos)?"), "status_pedido"
        return status_do_pedido(pedido), "status_pedido"
    return None


def status_do_pedido(pedido: dict) -> str:
    partes = [f"Pedido **{pedido['numero']}** ({pedido['itens']}): **{pedido['status']}**."]
    if pedido["data_entrega"]:
        partes.append(f"Entregue em {date.fromisoformat(pedido['data_entrega']).strftime('%d/%m/%Y')}.")
    elif pedido["previsao_entrega"]:
        partes.append(f"Previsão de entrega: {date.fromisoformat(pedido['previsao_entrega']).strftime('%d/%m/%Y')}.")
    if pedido["rastreio"] and pedido["status"] != "entregue":
        partes.append(f"Código de rastreio: {pedido['rastreio']}.")
    partes.append("Posso ajudar com mais alguma coisa nesse pedido?")
    return " ".join(partes)


def transferencia_para_humano(protocolo: str) -> str:
    hoje = date.today()
    return (
        f"Certo, vou transferir você para um atendente humano. Seu protocolo é **{protocolo}** — "
        "o atendente recebe todo o histórico desta conversa, então você não precisa repetir nada.\n\n"
        f"Hoje ({_nome_do_dia(hoje)}) o atendimento humano funciona "
        + ("das 9h às 18h" if hoje.weekday() == 6 else "das 8h às 22h")
        + " pelo chat, pelo WhatsApp (11) 4000-0000 ou pelo SAC 0800 000 0000."
    )
