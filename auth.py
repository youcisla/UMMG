"""Bearer-token authentication.

Accepts either `Authorization: Bearer <token>` or `x-api-key: <token>` and
compares against GATEWAY_BEARER_TOKEN using constant-time comparison.

The validated token is then passed through to upstream headroom in the
same header the client used, so existing clients (Claude Desktop, etc.)
see no behavior change for the upstreams they target.
"""
from __future__ import annotations

import hmac
from fastapi import Header, HTTPException, Request, status


def _extract_token(authorization: str | None, x_api_key: str | None) -> str | None:
    token: str | None = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value.strip()
    if not token and x_api_key:
        token = x_api_key.strip()
    return token


async def require_bearer(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> str:
    """FastAPI dependency. Returns the validated bearer token so handlers
    can pass it through to upstream. 401 on missing or wrong token."""
    expected = getattr(request.app.state, "bearer_token", None)
    if not expected:
        # Settings not loaded — fail closed.
        raise HTTPException(status_code=500, detail="Gateway auth not initialized")

    token = _extract_token(authorization, x_api_key)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token. Provide Authorization: Bearer <token> or x-api-key: <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def check_token(token: str, expected: str) -> bool:
    """Constant-time token comparison helper."""
    return hmac.compare_digest(token or "", expected or "")