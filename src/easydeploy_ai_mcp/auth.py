"""
auth.py — OAuth resource-server primitives for the MCP HTTP transport.

Validates Cognito **access** JWTs locally against the Cognito JWKS so the MCP
server can act as an OAuth 2.0 protected resource (RFC 9728). API keys
(``eda_live_*`` prefix) are NOT validated here — they are forwarded verbatim
to the EasyDeploy REST API, which is the source of truth for API key state.

Configuration (all required when ``EDA_OAUTH_ENABLED=1``):
    EDA_COGNITO_USER_POOL_ID    e.g. ``us-east-1_XXXXXXXXX``
    EDA_COGNITO_CLIENT_ID       e.g. ``7gbpkrusg9j2lhtoor4vj7uim4``
    EDA_COGNITO_REGION          e.g. ``us-east-1``

Cognito access tokens carry ``client_id`` (NOT ``aud``). We verify
``client_id``, ``token_use == 'access'``, issuer, and signature/expiry.

The ``pyjwt[crypto]`` dependency is optional and only required when OAuth
mode is enabled. Install with::

    pip install easydeploy-ai-mcp[oauth]
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

API_KEY_PREFIX = "eda_live_"


class AuthError(Exception):
    """Raised when bearer token validation fails. Carries an HTTP status."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OAuthConfig:
    user_pool_id: str
    client_id: str
    region: str

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


def is_oauth_enabled() -> bool:
    return os.environ.get("EDA_OAUTH_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def load_oauth_config() -> OAuthConfig:
    """Load Cognito config from env. Raises RuntimeError on missing values."""
    try:
        return OAuthConfig(
            user_pool_id=os.environ["EDA_COGNITO_USER_POOL_ID"].strip(),
            client_id=os.environ["EDA_COGNITO_CLIENT_ID"].strip(),
            region=os.environ.get("EDA_COGNITO_REGION", "us-east-1").strip(),
        )
    except KeyError as e:
        raise RuntimeError(
            f"OAuth mode requires {e.args[0]!r}. Set EDA_COGNITO_USER_POOL_ID and "
            "EDA_COGNITO_CLIENT_ID, or unset EDA_OAUTH_ENABLED."
        ) from e


def looks_like_api_key(token: str) -> bool:
    return token.startswith(API_KEY_PREFIX)


@lru_cache(maxsize=4)
def _jwk_client(jwks_uri: str):  # type: ignore[no-untyped-def]
    """Cached PyJWKClient. Imported lazily so pyjwt is optional."""
    try:
        from jwt import PyJWKClient  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover - exercised by env without extras
        raise RuntimeError(
            "pyjwt[crypto] is required for OAuth mode. "
            "Install with: pip install easydeploy-ai-mcp[oauth]"
        ) from e
    return PyJWKClient(jwks_uri, cache_keys=True)


def verify_cognito_access_token(token: str, config: OAuthConfig) -> dict[str, Any]:
    """Validate a Cognito access JWT. Returns decoded claims on success.

    Raises ``AuthError`` on any validation failure. Mirrors the checks the
    REST API authorizer performs (issuer, signature, exp, ``client_id``,
    ``token_use=='access'``) so a token accepted here will also be accepted
    downstream.
    """
    try:
        import jwt  # type: ignore[import-not-found]
        from jwt import InvalidTokenError  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "pyjwt[crypto] is required for OAuth mode. "
            "Install with: pip install easydeploy-ai-mcp[oauth]"
        ) from e

    try:
        signing_key = _jwk_client(config.jwks_uri).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=config.issuer,
            options={"require": ["exp", "iss", "token_use", "client_id"]},
        )
    except InvalidTokenError as e:
        raise AuthError(f"invalid_token: {e}") from e
    except Exception as e:  # JWKS fetch / network failures
        raise AuthError(f"invalid_token: {e}") from e

    if claims.get("token_use") != "access":
        raise AuthError("invalid_token: token_use must be 'access'")
    if claims.get("client_id") != config.client_id:
        raise AuthError("invalid_token: client_id mismatch")
    return claims
