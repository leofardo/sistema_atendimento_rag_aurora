"""Formatos de entrada e saída da API — é daqui que o /docs mostra o que cada rota recebe e devolve.

As saídas aceitam campos extras (extra="allow"): o esquema documenta o
essencial sem cortar um campo que a rota devolva a mais.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Saida(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- conversas ----------------------------------------------------------------
class ConversaCriada(Saida):
    id: str = Field(description="Identificador da conversa.")
    protocolo: str = Field(description="Número de protocolo do atendimento (Decreto 11.034/2022).")


class ConversaResumo(Saida):
    id: str
    protocolo: str
    titulo: str | None = Field(None, description="Primeira pergunta do cliente.")
    atualizadaEm: str | None = Field(None, description="Última mensagem, em UTC (AAAA-MM-DD hh:mm:ss).")
    mensagens: int = Field(description="Quantidade de mensagens (cliente + assistente).")
    brl: float = Field(description="Quanto a conversa custou, em reais.")


class ListaDeConversas(Saida):
    conversas: list[ConversaResumo]


class Mensagem(Saida):
    autor: str = Field(description='"cliente" ou "assistente".')
    texto: str
    meta: dict[str, Any] | None = Field(
        None, description="Só nas respostas: rota, intenção, custos, fontes e tempos.")


class ConversaDetalhe(Saida):
    id: str
    protocolo: str
    resumo: str = Field(description="Resumo das mensagens antigas, que saíram da janela do histórico.")
    mensagens: list[Mensagem]
    memoria: str = Field(description='Onde a conversa está guardada: "SQLite" ou "Redis".')


class NovaMensagem(BaseModel):
    texto: str = Field(min_length=1, max_length=2000, description="Mensagem do cliente.",
                       examples=["Quero devolver uma compra feita pelo site"])


# --- custos -------------------------------------------------------------------
class ResumoDeCustos(Saida):
    totalBrl: float
    totalUsd: float
    atendimentoBrl: float = Field(description="Gasto das conversas.")
    construcaoBrl: float = Field(description="Indexação da base, avaliações e dataset de fine-tuning.")
    totalTurnos: int = Field(description="Mensagens do cliente atendidas no período.")
    totalChamadas: int
    cotacao: float = Field(description="Dólar em reais usado na conversão.")
    porOperacao: list[dict[str, Any]]
    porRota: list[dict[str, Any]]
    porIntencao: list[dict[str, Any]]


class ListaDeChamadas(Saida):
    chamadas: list[dict[str, Any]] = Field(description="Cada chamada à OpenAI, com tokens e custo.")


class ListaDeMensagensComCusto(Saida):
    mensagens: list[dict[str, Any]] = Field(description="Cada pergunta do cliente, com rota, intenção e custo.")


class TextoParaEstimar(BaseModel):
    texto: str = Field(description="Texto que seria enviado como entrada (ex.: um prompt novo).")
    modelo: str | None = Field(None, description="Modelo; padrão: o modelo de resposta.")


class Estimativa(Saida):
    modelo: str
    tokens: int
    usd: float
    brl: float


# --- base de conhecimento --------------------------------------------------------
class ListaDeDocumentos(Saida):
    documentos: list[dict[str, Any]]
    totalTrechos: int
    cacheRespostas: int = Field(description="Respostas guardadas no cache semântico.")


class ListaDeTrechos(Saida):
    trechos: list[dict[str, Any]]
    custo: dict[str, Any] | None = Field(None, description="Só na busca: custo do embedding da consulta.")


class ResultadoDaIndexacao(Saida):
    trechos: int
    novos: int = Field(description="Trechos novos ou alterados (pagaram embedding).")
    reaproveitados: int = Field(description="Trechos sem mudança (custo zero).")
    removidos: int
    tokensEmbedding: int
    cacheLimpo: int | None = Field(None, description="Respostas apagadas do cache porque a base mudou.")


class ListaDoCache(Saida):
    itens: list[dict[str, Any]]


class CacheLimpo(Saida):
    removidos: int


# --- pedidos --------------------------------------------------------------------
class Pedido(Saida):
    numero: str
    cliente: str
    canal: str
    status: str
    itens: str
    valor: float
    data_compra: str
    previsao_entrega: str | None = None
    data_entrega: str | None = None
    rastreio: str | None = None


class ListaDePedidos(Saida):
    pedidos: list[Pedido]
