"""Acesso com senha única (AUTH_SENHA no backend/.env).

Todas as rotas /api exigem a senha no cabeçalho X-Senha (esquema "API Key").
- No /docs: botão "Authorize", um campo só (a senha). Depois disso o Swagger
  manda o cabeçalho em todo "Try it out". O /docs em si fica aberto.
- No frontend: a senha vem do frontend/.env (API_SENHA) e vai sozinha em toda
  requisição — quem usa o chat não digita nada.

O 401 NÃO manda o cabeçalho WWW-Authenticate: com ele, o navegador abriria a
própria janela de login a cada "Try it out" sem credencial.

A comparação usa secrets.compare_digest, que leva o mesmo tempo acerte ou
erre: não dá para descobrir a senha medindo o tempo da resposta.
"""

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

esquema_senha = APIKeyHeader(name="X-Senha", scheme_name="Senha", auto_error=False,
                             description="Senha de acesso (AUTH_SENHA do backend/.env).")


def _senha() -> str:
    senha = os.getenv("AUTH_SENHA", "").strip()
    if not senha:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "AUTH_SENHA não configurada em backend/.env.")
    return senha


def exigir_acesso(senha: str | None = Depends(esquema_senha)) -> None:
    """Dependência das rotas protegidas: a senha no cabeçalho X-Senha."""
    if senha and secrets.compare_digest(senha.encode(), _senha().encode()):
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Acesso não autorizado: use o botão Authorize do /docs.")
