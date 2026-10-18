"""O fluxo de um turno de atendimento, do mais barato para o mais caro.

    mensagem
      │
      ├─ 1. REGRA (R$ 0): saudação, obrigado, menu, horário, humano, "pedido 100231"
      │
      ├─ 2. ROTEADOR (nano, ~R$ 0,0003): intenção + consulta reescrita + dados extraídos
      │      └─ transferir_humano → mensagem de transferência (sem mais custo)
      │
      ├─ 3. DADOS DO SISTEMA (R$ 0): pedido do banco + prazos calculados em Python
      │
      ├─ 4. EMBEDDING da consulta (~R$ 0,000002)
      │      ├─ CACHE SEMÂNTICO: pergunta genérica já respondida → devolve (sem LLM)
      │      └─ BUSCA HÍBRIDA: vetorial + BM25 → RRF → filtro de relevância → top-4
      │             └─ nada relevante e sem dados → resposta curta pronta (sem LLM)
      │
      └─ 5. RESPOSTA (mini, streaming): prompt fixo em cache + trechos + pergunta

Cada passo que gasta registra a sua linha no livro-caixa; o turno inteiro
fica ligado pelo turno_id. Os eventos saem em NDJSON para o frontend:
inicio → delta... → fim (com custos, rota, intenção e fontes).
"""

import re
import time
from datetime import date
from typing import Any, AsyncIterator

from app.nucleo.configuracao import (
    limite_caracteres_historico,
    max_tokens_resposta,
    mensagens_no_historico,
    modelo_embedding,
    modelo_resposta,
    modelo_roteador,
)
from app.nucleo.precos import calcular, contar_tokens, contar_tokens_das_mensagens
from app.rag import cache_semantico
from app.rag.indice import Resultado, obter_indice, referencias_explicitas
from app.repositorios import custos, pedidos
from app.repositorios.memoria import obter_memoria, resumo_e_janela
from app.servicos import openai_cliente, prompt_resposta, regras, roteador
from app.servicos.cotacao import atualizar_cotacao
from app.servicos.prazos import calcular_prazos

# Intenções cuja resposta é igual para qualquer cliente: podem ir para o cache.
INTENCOES_GENERICAS = {
    "arrependimento_devolucao", "troca", "defeito_garantia", "cancelamento", "reembolso", "pagamento",
    "entrega_frete", "preco_cupom", "garantia_estendida", "instalacao", "atendimento_sac",
    "direitos_consumidor", "privacidade_dados",
}
# Abaixo disto de mensagens fora da janela, ainda não vale pagar um resumo.
MINIMO_PARA_RESUMIR = 4
# Intenções em que o número do pedido citado antes na conversa continua valendo.
INTENCOES_DE_PEDIDO = {
    "status_pedido", "atraso_entrega", "cancelamento", "arrependimento_devolucao", "troca",
    "defeito_garantia", "reembolso", "entrega_frete", "instalacao", "outro",
}


def _ultimo_pedido_citado(mensagens: list[dict[str, str]]) -> str | None:
    """Número de pedido (6 dígitos, existente no banco) mais recente na conversa."""
    for mensagem in reversed(mensagens):
        for numero in reversed(re.findall(r"\b(\d{6})\b", mensagem["texto"])):
            if pedidos.buscar(numero):
                return numero
    return None


def _fontes(trechos: list[Resultado]) -> list[dict[str, Any]]:
    return [
        {"fonte": t.fonte, "referencia": t.referencia, "caminho": t.caminho, "texto": t.texto,
         "similaridade": t.similaridade, "posicaoVetorial": t.posicao_vetorial,
         "posicaoBm25": t.posicao_bm25, "url": t.url}
        for t in trechos
    ]


def _dados_do_sistema(decisao: dict[str, Any], hoje: date) -> list[str]:
    """Pedido e prazos calculados — o que o modelo erraria se tivesse que contar."""
    linhas: list[str] = []
    numero = decisao.get("numero_pedido")
    pedido = pedidos.buscar(numero) if numero else None
    if numero and not pedido:
        linhas.append(f"pedido {numero}: NÃO ENCONTRADO no sistema (peça para o cliente conferir o número)")
    if pedido:
        linhas.append(pedidos.linha_compacta(pedido))
        recebimento = pedidos.data(pedido["data_entrega"])
        if recebimento:
            linhas += calcular_prazos(recebimento, hoje, pedido["canal"], decisao.get("produto_duravel"),
                                      pedidos.data(pedido["data_compra"]))
        elif pedido["previsao_entrega"]:
            previsao = pedidos.data(pedido["previsao_entrega"])
            if previsao and previsao < hoje:
                linhas.append(f"entrega ATRASADA: previsão era {previsao.strftime('%d/%m/%Y')}")
    elif decisao.get("data_referencia"):
        referencia = pedidos.data(decisao["data_referencia"])
        if referencia and referencia <= hoje:
            linhas.append(f"data informada pelo cliente: {referencia.strftime('%d/%m/%Y')} "
                          "(canal da compra desconhecido; considere compra online se ele não disse)")
            linhas += calcular_prazos(referencia, hoje, None, decisao.get("produto_duravel"))
    return linhas


def _custo_da_resposta(uso: dict[str, Any] | None, mensagens: list[dict[str, Any]], texto: str, modelo: str) -> dict[str, Any]:
    if uso:
        custo = calcular(modelo, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0),
                         (uso.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        custo["medidoPelaApi"] = True
        return custo
    custo = calcular(modelo, contar_tokens_das_mensagens(mensagens, modelo), contar_tokens(texto, modelo))
    custo["medidoPelaApi"] = False
    return custo


async def atender(sessao: str, mensagem: str) -> AsyncIterator[dict[str, Any]]:
    await atualizar_cotacao()
    memoria = obter_memoria()
    protocolo = memoria.protocolo(sessao)
    primeira = memoria.total(sessao) == 0
    turno_id = custos.abrir_turno(sessao, mensagem, protocolo)
    inicio = time.monotonic()
    hoje = date.today()
    custos_do_turno: list[dict[str, Any]] = []
    estado: dict[str, Any] = {"rota": "erro", "intencao": None, "trechos": [], "fontes": [], "texto": "", "cache": None}

    def encerrar() -> dict[str, Any]:
        """Fecha o turno no livro-caixa, guarda a resposta COM seus metadados e devolve o evento final.

        Os metadados (custos, rota, fontes) ficam junto da mensagem para o
        histórico de conversas reabrir uma conversa antiga exatamente como foi.
        """
        duracao = int((time.monotonic() - inicio) * 1000)
        custos.fechar_turno(turno_id, estado["rota"], estado["intencao"], len(estado["trechos"]), duracao)
        evento = {
            "tipo": "fim", "turnoId": turno_id, "protocolo": protocolo, "rota": estado["rota"],
            "rotuloRota": custos.ROTULOS_DE_ROTA.get(estado["rota"], estado["rota"]),
            "intencao": estado["intencao"], "custos": custos_do_turno, "fontes": estado["fontes"],
            "cache": estado["cache"], "duracaoMs": duracao,
            # O que o cliente sente é a espera até a PRIMEIRA palavra, não até a última.
            "primeiroTextoMs": estado.get("primeiro_ms") or duracao,
            "totalBrl": sum(c["brl"] for c in custos_do_turno), "totalUsd": sum(c["usd"] for c in custos_do_turno),
        }
        if estado["texto"]:
            memoria.adicionar(sessao, "assistente", estado["texto"], {k: v for k, v in evento.items() if k != "tipo"})
        return evento

    def responder_pronto(texto: str) -> dict[str, Any]:
        estado["texto"] = texto
        estado.setdefault("primeiro_ms", int((time.monotonic() - inicio) * 1000))
        return {"tipo": "delta", "texto": texto}

    yield {"tipo": "inicio", "protocolo": protocolo, "turnoId": turno_id}
    memoria.adicionar(sessao, "cliente", mensagem)

    # 1. Regra: custo zero.
    anteriores = [m for m in memoria.conversa_completa(sessao) if m["autor"] == "assistente"] if not primeira else []
    intencao_anterior = ((anteriores[-1].get("meta") or {}).get("intencao") or "outro") if anteriores else None
    por_regra = regras.responder_por_regra(mensagem, protocolo, primeira, intencao_anterior)
    if por_regra:
        estado["rota"], estado["intencao"] = ("humano" if por_regra[1] == "falar_humano" else "regra"), por_regra[1]
        yield responder_pronto(por_regra[0])
        yield encerrar()
        return

    # A janela inclui a mensagem que acabou de entrar (+1). O que saiu da janela
    # e ainda não foi resumido (no máximo MINIMO_PARA_RESUMIR - 1) vai junto.
    resumo, pendentes, janela, _ = resumo_e_janela(memoria, sessao, mensagens_no_historico() + 1)
    historico = pendentes + janela[:-1]

    try:
        # 2. Roteador.
        decisao, custo, duracao = await roteador.rotear(mensagem, historico, hoje)
        custos_do_turno.append(custos.registrar("roteamento", custo, duracao, sessao, turno_id))
        # Trava em código: artigo citado pelo número SEMPRE consulta a base, mesmo
        # se o roteador achar que é fora do escopo (o teste real pegou esse erro).
        if referencias_explicitas(mensagem) and not decisao.get("precisa_base"):
            decisao.update(precisa_base=True, consulta=decisao.get("consulta") or mensagem,
                           intencao="direitos_consumidor")
        # Trava em código: mensagem curta no meio da conversa ("e geladeira?") é
        # continuação, não assunto novo. O roteador já errou isso no teste real.
        # Se for mesmo fora do assunto, a busca não acha nada e cai na resposta curta pronta.
        if decisao.get("intencao") == "fora_do_escopo" and historico and len(mensagem.split()) <= 5:
            anterior = next((m["texto"] for m in reversed(historico) if m["autor"] == "cliente"), "")
            decisao.update(intencao="outro", precisa_base=True, consulta=f"{anterior} {mensagem}"[:300])
        # O roteador só vê as últimas 4 falas; o número do pedido pode ter ficado
        # para trás ("e se não chegar hoje?" pedia o número de novo). O código lembra.
        if not decisao.get("numero_pedido") and decisao.get("intencao") in INTENCOES_DE_PEDIDO:
            decisao["numero_pedido"] = _ultimo_pedido_citado(memoria.mensagens(sessao))
        estado["intencao"] = decisao.get("intencao")

        if decisao.get("intencao") == "fora_do_escopo" and not decisao.get("precisa_base"):
            # Recusar não precisa de redação: frase pronta, sem o modelo de resposta.
            estado["rota"] = "regra"
            yield responder_pronto(regras.FORA_DO_ESCOPO)
            yield encerrar()
            return

        if decisao.get("intencao") in ("status_pedido", "atraso_entrega") and not decisao.get("numero_pedido"):
            # "Como está meu pedido?" sem número: a única resposta útil é pedir o
            # número. Antes isso passava pelo RAG e custava R$ 0,005 para dizer isso.
            estado["rota"] = "regra"
            yield responder_pronto(regras.PEDIR_NUMERO_DO_PEDIDO)
            yield encerrar()
            return

        pedido = pedidos.buscar(decisao["numero_pedido"]) if decisao.get("numero_pedido") else None
        ultima_resposta = next((m["texto"] for m in reversed(historico) if m["autor"] == "assistente"), "")
        if (decisao.get("intencao") == "status_pedido" and pedido and regras.pede_status(mensagem)
                and regras.status_do_pedido(pedido) != ultima_resposta):
            # Status é leitura do banco: o texto pronto é exato e não custa a resposta.
            # Só quando a mensagem ATUAL pede status e sem repetir a última resposta;
            # senão ("como funciona o prazo?") o modelo responde com o pedido como contexto.
            estado["rota"] = "regra"
            yield responder_pronto(regras.status_do_pedido(pedido))
            yield encerrar()
            return

        if decisao.get("transferir_humano") or decisao.get("intencao") == "falar_humano":
            estado["rota"] = "humano"
            yield responder_pronto(regras.transferencia_para_humano(protocolo))
            yield encerrar()
            return

        # 3. Dados que o código calcula.
        dados_sistema = _dados_do_sistema(decisao, hoje)

        # 4. Embedding, cache e busca.
        trechos: list[Resultado] = []
        consulta = (decisao.get("consulta") or "").strip()
        if decisao.get("precisa_base") and consulta:
            # Cache só para a PRIMEIRA pergunta de conteúdo, sem dado do cliente: a
            # resposta depende só da mensagem. O teste real mostrou o contrário
            # vazando — uma resposta com "air fryer" (dita antes) servida a outro cliente.
            pode_cachear = (not dados_sistema and not historico and not resumo
                            and decisao.get("intencao") in INTENCOES_GENERICAS)
            t0 = time.monotonic()
            # Mesma requisição: vetor da consulta (busca) e, se cacheável, da mensagem (chave do cache).
            vetores, tokens = await openai_cliente.embeddings([consulta, mensagem] if pode_cachear else [consulta])
            vetor = vetores[0]
            vetor_cache = vetores[1] if pode_cachear else None
            custos_do_turno.append(custos.registrar(
                "embedding", calcular(modelo_embedding(), tokens), int((time.monotonic() - t0) * 1000), sessao, turno_id))

            if pode_cachear:
                achado = cache_semantico.buscar(vetor_cache)
                if achado:
                    estado["rota"], estado["cache"] = "cache", achado
                    yield responder_pronto(achado["resposta"])
                    estado["fontes"] = achado["fontes"]
                    yield encerrar()
                    return

            trechos = obter_indice().buscar(consulta, vetor, texto_original=mensagem)
            estado["trechos"], estado["fontes"] = trechos, _fontes(trechos)
            if not trechos and not dados_sistema:
                estado["rota"] = "nao_encontrado"
                yield responder_pronto(
                    "Não encontrei essa informação nas políticas da Aurora nem nas regras de atendimento que eu "
                    f"consulto. Posso transferir você para um atendente humano, mantendo o protocolo **{protocolo}**. "
                    "Se quiser, reformule a pergunta com mais detalhes (produto, canal da compra, número do pedido)."
                )
                yield encerrar()
                return
        else:
            pode_cachear = False
            vetor_cache = None

        # 5. Resposta em streaming.
        estado["rota"] = "rag" if trechos else "sem_base"
        modelo = modelo_resposta()
        mensagens = prompt_resposta.montar_mensagens(
            mensagem, protocolo, hoje, resumo, historico, limite_caracteres_historico(), dados_sistema, trechos)
        uso: dict[str, Any] = {}
        partes: list[str] = []
        t0 = time.monotonic()
        async for pedaco in openai_cliente.chat_stream(mensagens, modelo, max_tokens_resposta(), uso):
            if not partes:
                estado["primeiro_ms"] = int((time.monotonic() - inicio) * 1000)
            partes.append(pedaco)
            yield {"tipo": "delta", "texto": pedaco}
        estado["texto"] = "".join(partes).strip()
        custos_do_turno.append(custos.registrar(
            "resposta", _custo_da_resposta(uso.get("uso"), mensagens, estado["texto"], modelo),
            int((time.monotonic() - t0) * 1000), sessao, turno_id))

        if not estado["texto"]:
            estado["texto"] = "Desculpe, não consegui gerar a resposta agora. Pode tentar de novo?"
            yield {"tipo": "delta", "texto": estado["texto"]}
        elif pode_cachear and vetor_cache is not None and trechos:
            cache_semantico.guardar(vetor_cache, mensagem, estado["texto"], _fontes(trechos))

        yield encerrar()
    except openai_cliente.ErroOpenAI as erro:
        estado["rota"] = "erro"
        yield {"tipo": "erro", "mensagem": str(erro)}
        yield encerrar()


async def resumir_se_preciso(sessao: str) -> None:
    """Fora da janela de histórico, as mensagens viram um resumo curto (nano).

    Roda DEPOIS da resposta (BackgroundTask), para não atrasar o cliente. Só
    paga quando há pelo menos MINIMO_PARA_RESUMIR mensagens novas fora da
    janela — resumir a cada mensagem custaria mais do que economiza.
    """
    memoria = obter_memoria()
    resumo, pendentes, _, marcador = resumo_e_janela(memoria, sessao, mensagens_no_historico())
    if len(pendentes) < MINIMO_PARA_RESUMIR:
        return
    conversa = "\n".join(f"{'Cliente' if m['autor'] == 'cliente' else 'Lia'}: {m['texto'][:400]}" for m in pendentes)
    conteudo = (f"Resumo anterior: {resumo}\n\n" if resumo else "") + f"Novas mensagens:\n{conversa}"
    modelo = modelo_roteador() if not modelo_roteador().startswith("ft:") else "gpt-4.1-nano"
    t0 = time.monotonic()
    try:
        texto, uso = await openai_cliente.chat_texto(
            [{"role": "system", "content": prompt_resposta.PROMPT_RESUMO}, {"role": "user", "content": conteudo}],
            modelo, max_tokens=150)
    except openai_cliente.ErroOpenAI:
        return
    custo = calcular(modelo, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0),
                     (uso.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    custos.registrar("resumo", custo, int((time.monotonic() - t0) * 1000), sessao, None)
    memoria.salvar_resumo(sessao, texto, marcador)
