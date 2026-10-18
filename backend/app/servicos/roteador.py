"""Roteador: UMA chamada barata (nano) que faz quatro trabalhos de uma vez.

1. Classifica a INTENÇÃO (para métricas de custo e para decidir a rota).
2. Decide se precisa da base de conhecimento (saudação/conversa não precisa).
3. REESCREVE a pergunta como consulta de busca autônoma. "E se já passou?"
   não acha nada num banco vetorial; "prazo de arrependimento vencido compra
   online" acha. É a técnica de *query rewriting* (e o "condense question" do
   LangChain) — e faz o papel do HyDE/Multi-Query do curso SEM uma chamada a
   mais: o mesmo nano que classifica já escreve a consulta no vocabulário dos
   documentos.
4. EXTRAI número do pedido, data de compra/recebimento e se o produto é
   durável — para o código calcular prazos (app.servicos.prazos).

Por que não deixar o modelo de resposta fazer isso via tool calling: seriam
duas rodadas no modelo mini (a da ferramenta e a da resposta). Uma rodada no
nano + uma no mini sai mais barato.

FINE-TUNING: o prompt longo abaixo (~900 tokens) viaja em toda mensagem. Um
nano afinado com exemplos deste roteador aprende o formato e as regras e
passa a funcionar com o PROMPT_CURTO (~40 tokens). Ver
scripts/finetuning_roteador.py.
"""

import time
from datetime import date
from typing import Any

from app.nucleo.configuracao import modelo_roteador
from app.nucleo.precos import calcular
from app.servicos import openai_cliente

INTENCOES = [
    "saudacao", "agradecimento", "status_pedido", "atraso_entrega", "arrependimento_devolucao",
    "troca", "defeito_garantia", "cancelamento", "reembolso", "pagamento", "entrega_frete",
    "preco_cupom", "garantia_estendida", "instalacao", "atendimento_sac", "direitos_consumidor",
    "privacidade_dados", "reclamacao", "falar_humano", "fora_do_escopo", "outro",
]

ESQUEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intencao", "precisa_base", "consulta", "numero_pedido", "data_referencia",
                 "produto_duravel", "transferir_humano"],
    "properties": {
        "intencao": {"type": "string", "enum": INTENCOES},
        "precisa_base": {"type": "boolean"},
        "consulta": {"type": "string"},
        "numero_pedido": {"type": ["string", "null"]},
        "data_referencia": {"type": ["string", "null"]},
        "produto_duravel": {"type": ["boolean", "null"]},
        "transferir_humano": {"type": "boolean"},
    },
}

PROMPT_COMPLETO = f"""Você é o roteador do atendimento da Aurora Casa & Tech, varejista de eletrodomésticos, eletrônicos e utilidades (site, app, telefone e lojas físicas). Você NÃO responde ao cliente: só analisa a última mensagem dele e devolve o JSON.

Campos:
- intencao: uma de {", ".join(INTENCOES)}.
- precisa_base: true se a resposta depende de política da empresa ou de lei (prazos, regras, direitos, condições, canais, procedimentos). false para saudação, agradecimento, conversa casual, fora do escopo, ou quando o cliente só informa um dado sem pergunta nova.
- consulta: se precisa_base=true, reescreva a dúvida como uma consulta de busca AUTÔNOMA e completa, usando o histórico para resolver referências ("e se já passou?", "e no cartão?", "isso vale pra loja?"). Use o vocabulário dos documentos: "direito de arrependimento", "troca por cortesia", "garantia legal", "vício do produto", "estorno", "reembolso", "prazo de entrega", "cancelamento", "assistência técnica", "atendimento prioritário", "SAC", "protocolo". Inclua o canal da compra (online/loja) quando souber. NUNCA acrescente nome ou número de lei, decreto ou artigo que o cliente não citou (se ele citou, repita exatamente). Não inclua o nome da empresa. Máximo 25 palavras. Se precisa_base=false, "".
- numero_pedido: número do pedido citado na mensagem ou no histórico recente (6 dígitos), senão null.
- data_referencia: data de compra ou recebimento citada pelo cliente, no formato AAAA-MM-DD, resolvida a partir da data de hoje ("ontem", "dia 3", "semana passada" = 7 dias atrás). null se não houver.
- produto_duravel: true para eletrodoméstico, eletrônico, móvel, utensílio; false para produto que se consome rápido (alimento, cosmético, item descartável); null se não der para saber.
- transferir_humano: true se o cliente pede atendente/humano/pessoa, ou relata cobrança indevida, fraude, golpe, dano pessoal, risco à saúde, ou está muito irritado depois de já ter recebido uma resposta. Caso contrário, false.

Regras de intenção:
- "status_pedido": onde está, rastreio, situação. "atraso_entrega": passou do prazo, não chegou.
- "arrependimento_devolucao": desistir, devolver sem defeito, me arrependi. "troca": trocar cor/tamanho/voltagem/modelo.
- "defeito_garantia": parou de funcionar, chegou quebrado, com defeito, garantia legal ou do fabricante.
- "reembolso": estorno, dinheiro de volta, quando cai o valor. "cancelamento": cancelar pedido ainda não entregue.
- "direitos_consumidor": dúvida genérica sobre lei, CDC, direitos. "atendimento_sac": canais, horários, protocolo, regras do SAC.
- CONTINUAÇÃO: mensagem curta que depende do histórico ("e geladeira?", "e no cartão?", "e se for na loja?", "e o frete?") continua o assunto da última pergunta do cliente: mesma intenção, precisa_base igual, e a consulta junta o assunto anterior com o novo detalhe. Continuação NUNCA é fora do escopo.
- Produtos da Aurora (geladeira, fogão, TV, celular, notebook, air fryer, máquina de lavar, ar-condicionado, micro-ondas, fone etc.) nunca são fora do escopo.
- Pergunta sobre lei, decreto ou artigo (CDC, Decreto 11.034 do SAC, Decreto 7.962 do comércio eletrônico, Lei 10.048 do atendimento prioritário) NUNCA é fora do escopo: é "direitos_consumidor" ou "atendimento_sac", com precisa_base=true.
- "fora_do_escopo": assunto sem relação com compras na Aurora nem com direitos do consumidor (geografia, esporte, receitas, programação, produtos que a Aurora não vende como carros e imóveis).

Exemplos:
Mensagem: "comprei uma air fryer pelo site e me arrependi, posso devolver?"
{{"intencao":"arrependimento_devolucao","precisa_base":true,"consulta":"direito de arrependimento compra online devolução prazo 7 dias recebimento","numero_pedido":null,"data_referencia":null,"produto_duravel":true,"transferir_humano":false}}
Histórico: Cliente: comprei na loja do shopping uma camiseta dry fit | Mensagem: "dá pra trocar o tamanho?"
{{"intencao":"troca","precisa_base":true,"consulta":"troca por cortesia tamanho compra em loja física prazo condições","numero_pedido":null,"data_referencia":null,"produto_duravel":true,"transferir_humano":false}}
Histórico: Cliente: comprei uma air fryer pelo site e me arrependi, posso devolver? | Assistente: Pode sim, você tem 7 dias corridos do recebimento. | Mensagem: "e geladeira?"
{{"intencao":"arrependimento_devolucao","precisa_base":true,"consulta":"direito de arrependimento geladeira compra online devolução prazo 7 dias","numero_pedido":null,"data_referencia":null,"produto_duravel":true,"transferir_humano":false}}
Mensagem: "meu pedido 100262 a geladeira parou de gelar"
{{"intencao":"defeito_garantia","precisa_base":true,"consulta":"produto com defeito geladeira garantia legal assistência técnica produto essencial troca","numero_pedido":"100262","data_referencia":null,"produto_duravel":true,"transferir_humano":false}}
Mensagem: "vocês cobraram duas vezes no meu cartão, quero resolver isso agora"
{{"intencao":"reclamacao","precisa_base":true,"consulta":"cobrança indevida em duplicidade cartão devolução em dobro","numero_pedido":null,"data_referencia":null,"produto_duravel":null,"transferir_humano":true}}
Mensagem: "qual a capital da França?"
{{"intencao":"fora_do_escopo","precisa_base":false,"consulta":"","numero_pedido":null,"data_referencia":null,"produto_duravel":null,"transferir_humano":false}}"""

# Para o modelo afinado: as regras estão nos pesos, não no prompt.
PROMPT_CURTO = "Roteador do atendimento Aurora Casa & Tech. Analise a última mensagem do cliente e devolva o JSON."


def usa_prompt_curto(modelo: str) -> bool:
    return modelo.startswith("ft:")


def montar_entrada(mensagem: str, historico: list[dict[str, str]], hoje: date) -> str:
    """Texto variável do roteador: data, últimas falas (cortadas) e a mensagem."""
    linhas = [f"Hoje: {hoje.isoformat()}"]
    recentes = historico[-4:]
    if recentes:
        falas = " | ".join(
            f"{'Cliente' if m['autor'] == 'cliente' else 'Assistente'}: {m['texto'][:160]}" for m in recentes
        )
        linhas.append(f"Histórico: {falas}")
    linhas.append(f'Mensagem: "{mensagem}"')
    return "\n".join(linhas)


def mensagens_do_roteador(modelo: str, entrada: str) -> list[dict[str, str]]:
    sistema = PROMPT_CURTO if usa_prompt_curto(modelo) else PROMPT_COMPLETO
    return [{"role": "system", "content": sistema}, {"role": "user", "content": entrada}]


async def rotear(mensagem: str, historico: list[dict[str, str]], hoje: date) -> tuple[dict[str, Any], dict[str, Any], int]:
    """(decisão, custo, duração em ms)."""
    modelo = modelo_roteador()
    inicio = time.monotonic()
    decisao, uso = await openai_cliente.chat_json(
        mensagens_do_roteador(modelo, montar_entrada(mensagem, historico, hoje)), modelo, ESQUEMA, max_tokens=150
    )
    custo = calcular(
        modelo, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0),
        (uso.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
    )
    return decisao, custo, int((time.monotonic() - inicio) * 1000)
