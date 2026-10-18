"""Memória da conversa: protocolo, mensagens e resumo de cada sessão.

Duas implementações com a mesma interface, escolhidas pelo .env:

- SQLite (padrão): zero instalação, sobrevive a reinício, suficiente para um
  servidor só.
- Redis (REDIS_URL=redis://localhost:6379/0): faz sentido quando houver mais
  de uma instância da API atrás de um balanceador (todas leem a mesma
  memória), ou quando se quer expirar conversa sozinha por TTL. As mensagens
  ficam numa lista com LTRIM — o Redis guarda só a janela que o prompt usa.

O livro-caixa de custos NÃO vem para cá: ele precisa durar e ser consultado
com SQL (somas por período, por intenção), que é trabalho de banco relacional.
"""

import json
import random
import time
from datetime import datetime
from typing import Any, Protocol

from app.nucleo.banco import obter_conexao
from app.nucleo.configuracao import ttl_conversa_segundos, url_redis

# Guardamos mais que a janela do prompt para o resumo ter o que resumir.
MAXIMO_GUARDADO = 40


def titulo_da_conversa(primeira_pergunta: str) -> str:
    texto = " ".join(primeira_pergunta.split())
    return texto if len(texto) <= 48 else texto[:46].rsplit(" ", 1)[0] + "…"


def novo_protocolo() -> str:
    # Decreto 11.034/2022: todo atendimento do SAC recebe número de protocolo.
    return datetime.now().strftime("%Y%m%d") + f"{random.randint(0, 999999):06d}"


class Memoria(Protocol):
    def existe(self, sessao: str) -> bool: ...
    def protocolo(self, sessao: str) -> str: ...
    def adicionar(self, sessao: str, autor: str, texto: str, meta: dict[str, Any] | None = None) -> None: ...
    def mensagens(self, sessao: str) -> list[dict[str, str]]: ...
    def conversa_completa(self, sessao: str) -> list[dict[str, Any]]: ...
    def listar(self, limite: int) -> list[dict[str, Any]]: ...
    def apagar(self, sessao: str) -> None: ...
    def total(self, sessao: str) -> int: ...
    def resumo(self, sessao: str) -> tuple[str, int]: ...
    def salvar_resumo(self, sessao: str, resumo: str, resumidas: int) -> None: ...
    def nome(self) -> str: ...


class MemoriaSQLite:
    def nome(self) -> str:
        return "SQLite"

    def existe(self, sessao: str) -> bool:
        with obter_conexao() as conexao:
            return conexao.execute("SELECT 1 FROM conversas WHERE sessao = ?", (sessao,)).fetchone() is not None

    def protocolo(self, sessao: str) -> str:
        with obter_conexao() as conexao:
            linha = conexao.execute("SELECT protocolo FROM conversas WHERE sessao = ?", (sessao,)).fetchone()
            if linha:
                return linha["protocolo"]
            protocolo = novo_protocolo()
            conexao.execute("INSERT INTO conversas (sessao, protocolo) VALUES (?, ?)", (sessao, protocolo))
            return protocolo

    def adicionar(self, sessao: str, autor: str, texto: str, meta: dict[str, Any] | None = None) -> None:
        with obter_conexao() as conexao:
            conexao.execute("INSERT INTO mensagens (sessao, autor, texto, meta) VALUES (?, ?, ?, ?)",
                            (sessao, autor, texto, json.dumps(meta, ensure_ascii=False) if meta else None))
            # O nome da conversa é a primeira pergunta do cliente: de graça, sem
            # pedir para um modelo "resumir em um título".
            conexao.execute(
                "UPDATE conversas SET atualizada_em = datetime('now'), titulo = COALESCE(titulo, ?) WHERE sessao = ?",
                (titulo_da_conversa(texto) if autor == "cliente" else None, sessao),
            )

    def conversa_completa(self, sessao: str) -> list[dict[str, Any]]:
        with obter_conexao() as conexao:
            linhas = conexao.execute(
                "SELECT autor, texto, meta FROM mensagens WHERE sessao = ? ORDER BY id", (sessao,)).fetchall()
        return [{"autor": l["autor"], "texto": l["texto"], "meta": json.loads(l["meta"]) if l["meta"] else None}
                for l in linhas]

    def listar(self, limite: int) -> list[dict[str, Any]]:
        with obter_conexao() as conexao:
            linhas = conexao.execute(
                """
                SELECT c.sessao, c.protocolo, c.titulo, c.atualizada_em, COUNT(m.id) AS mensagens
                FROM conversas c JOIN mensagens m ON m.sessao = c.sessao
                GROUP BY c.sessao ORDER BY c.atualizada_em DESC LIMIT ?
                """,
                (limite,),
            ).fetchall()
        return [dict(l) for l in linhas]

    def apagar(self, sessao: str) -> None:
        # Só a conversa. O livro-caixa (ai_usage/turnos) fica: o dinheiro foi gasto.
        with obter_conexao() as conexao:
            conexao.execute("DELETE FROM mensagens WHERE sessao = ?", (sessao,))
            conexao.execute("DELETE FROM conversas WHERE sessao = ?", (sessao,))

    def mensagens(self, sessao: str) -> list[dict[str, str]]:
        with obter_conexao() as conexao:
            linhas = conexao.execute(
                "SELECT autor, texto FROM mensagens WHERE sessao = ? ORDER BY id DESC LIMIT ?",
                (sessao, MAXIMO_GUARDADO),
            ).fetchall()
        return [{"autor": l["autor"], "texto": l["texto"]} for l in reversed(linhas)]

    def total(self, sessao: str) -> int:
        with obter_conexao() as conexao:
            return conexao.execute("SELECT COUNT(*) FROM mensagens WHERE sessao = ?", (sessao,)).fetchone()[0]

    def resumo(self, sessao: str) -> tuple[str, int]:
        with obter_conexao() as conexao:
            linha = conexao.execute("SELECT resumo, resumidas FROM conversas WHERE sessao = ?", (sessao,)).fetchone()
        return (linha["resumo"], linha["resumidas"]) if linha else ("", 0)

    def salvar_resumo(self, sessao: str, resumo: str, resumidas: int) -> None:
        with obter_conexao() as conexao:
            conexao.execute("UPDATE conversas SET resumo = ?, resumidas = ? WHERE sessao = ?", (resumo, resumidas, sessao))


class MemoriaRedis:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._r.ping()  # falha cedo, na subida da API, se o Redis não estiver de pé
        self._ttl = ttl_conversa_segundos()

    def nome(self) -> str:
        return "Redis"

    def existe(self, sessao: str) -> bool:
        return bool(self._r.exists(f"conversa:{sessao}"))

    def _renovar(self, sessao: str) -> None:
        for chave in (f"conversa:{sessao}", f"mensagens:{sessao}"):
            self._r.expire(chave, self._ttl)

    def protocolo(self, sessao: str) -> str:
        chave = f"conversa:{sessao}"
        protocolo = self._r.hget(chave, "protocolo")
        if not protocolo:
            protocolo = novo_protocolo()
            self._r.hset(chave, mapping={"protocolo": protocolo, "resumo": "", "resumidas": 0})
        self._renovar(sessao)
        return protocolo

    def adicionar(self, sessao: str, autor: str, texto: str, meta: dict[str, Any] | None = None) -> None:
        chave = f"mensagens:{sessao}"
        agora = time.time()
        canal = self._r.pipeline()
        canal.rpush(chave, json.dumps({"autor": autor, "texto": texto, "meta": meta, "em": agora}, ensure_ascii=False))
        canal.ltrim(chave, -MAXIMO_GUARDADO, -1)
        canal.hincrby(f"conversa:{sessao}", "total", 1)
        if autor == "cliente":
            canal.hsetnx(f"conversa:{sessao}", "titulo", titulo_da_conversa(texto))
        canal.hset(f"conversa:{sessao}", "atualizada_em", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        # Índice das conversas por data, para o menu lateral listar sem varrer chaves.
        canal.zadd("conversas", {sessao: agora})
        canal.execute()
        self._renovar(sessao)

    def mensagens(self, sessao: str) -> list[dict[str, str]]:
        brutas = self._r.lrange(f"mensagens:{sessao}", 0, -1)
        return [{"autor": d["autor"], "texto": d["texto"]} for d in map(json.loads, brutas)]

    def conversa_completa(self, sessao: str) -> list[dict[str, Any]]:
        # No Redis guardamos só as últimas MAXIMO_GUARDADO mensagens (LTRIM).
        brutas = self._r.lrange(f"mensagens:{sessao}", 0, -1)
        return [{"autor": d["autor"], "texto": d["texto"], "meta": d.get("meta")} for d in map(json.loads, brutas)]

    def listar(self, limite: int) -> list[dict[str, Any]]:
        conversas = []
        for sessao in self._r.zrevrange("conversas", 0, limite - 1):
            dados = self._r.hgetall(f"conversa:{sessao}")
            if not dados:  # expirou pelo TTL
                self._r.zrem("conversas", sessao)
                continue
            conversas.append({"sessao": sessao, "protocolo": dados.get("protocolo"), "titulo": dados.get("titulo"),
                              "atualizada_em": dados.get("atualizada_em"), "mensagens": int(dados.get("total", 0))})
        return conversas

    def apagar(self, sessao: str) -> None:
        self._r.delete(f"conversa:{sessao}", f"mensagens:{sessao}")
        self._r.zrem("conversas", sessao)

    def total(self, sessao: str) -> int:
        return int(self._r.hget(f"conversa:{sessao}", "total") or 0)

    def resumo(self, sessao: str) -> tuple[str, int]:
        dados = self._r.hgetall(f"conversa:{sessao}")
        return dados.get("resumo", ""), int(dados.get("resumidas", 0) or 0)

    def salvar_resumo(self, sessao: str, resumo: str, resumidas: int) -> None:
        self._r.hset(f"conversa:{sessao}", mapping={"resumo": resumo, "resumidas": resumidas})


_memoria: Memoria | None = None


def obter_memoria() -> Memoria:
    global _memoria
    if _memoria is None:
        url = url_redis()
        _memoria = MemoriaRedis(url) if url else MemoriaSQLite()
    return _memoria


def resumo_e_janela(
    memoria: Memoria, sessao: str, janela: int
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int]:
    """(resumo atual, mensagens fora da janela ainda não resumidas, janela recente, novo marcador).

    As posições são absolutas (desde o início da conversa), porque a lista
    guardada é cortada nas últimas MAXIMO_GUARDADO mensagens.
    """
    todas = memoria.mensagens(sessao)
    total = memoria.total(sessao)
    resumo, resumidas = memoria.resumo(sessao)
    inicio = total - len(todas)
    limite = max(total - janela, 0)
    pendentes = [m for i, m in enumerate(todas, start=inicio) if resumidas <= i < limite]
    return resumo, pendentes, todas[-janela:] if janela else [], limite
