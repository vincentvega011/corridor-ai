"""Demo authentication: three fixed bearer tokens, one per persona (docs/spec/modules.md 5.16, P1 subset).

The authenticated user id comes ONLY from the token. Nothing in a request body, query string, header
other than Authorization, or model output can change it (INV-1, INV-4). Tokens live in secrets/
(gitignored) and are registered with the prompt guard.
"""

import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class TokenAuth:
    """Maps bearer tokens to user ids using constant time comparison."""

    tokens: dict[str, str]  # user id -> token

    def user_for(self, token: str) -> str | None:
        found: str | None = None
        for user_id, t in self.tokens.items():
            if hmac.compare_digest(
                t.encode(), token.encode()
            ):  # no early exit: constant time per token
                found = user_id
        return found


def current_user(request: Request) -> str:
    """FastAPI dependency: the user id for the Authorization: Bearer token, else 401."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    auth: TokenAuth = request.app.state.auth
    user = auth.user_for(token.strip()) if scheme.lower() == "bearer" and token.strip() else None
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def officer_id_for(request: Request, officers: dict[str, str] | str | None) -> str:
    """The officer id for a valid officer bearer token, else 401 (404 when officer access is off).

    ``officers`` maps officer id to credential. Only an officer credential opens officer routes: a customer
    token compares unequal to all of them, so a customer can never verify a payee, by chat, by settings or by
    calling this route (ADR 0012). A bare string is one officer, ``officer_demo_1`` (tests)."""
    if officers is None:
        raise HTTPException(status_code=404, detail="not found")
    table = {"officer_demo_1": officers} if isinstance(officers, str) else officers
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer":
        given = token.strip().encode()
        found = None
        for (
            oid,
            cred,
        ) in table.items():  # constant time compare against every officer, no early exit
            if hmac.compare_digest(given, cred.encode()):
                found = oid
        if found is not None:
            return found
    raise HTTPException(
        status_code=401, detail="officer token required", headers={"WWW-Authenticate": "Bearer"}
    )
