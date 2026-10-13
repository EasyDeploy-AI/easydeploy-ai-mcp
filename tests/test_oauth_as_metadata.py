"""Unit tests for OAuth AS metadata proxy helpers."""

from __future__ import annotations

import pytest

from easydeploy_ai_mcp import oauth_as_metadata


def test_build_proxy_authorization_server_metadata_minimal():
    body = oauth_as_metadata.build_proxy_authorization_server_metadata(
        mcp_issuer="https://mcp.example.com",
        cognito_oidc={
            "authorization_endpoint": "https://auth.example.com/oauth2/authorize",
            "token_endpoint": "https://auth.example.com/oauth2/token",
        },
    )
    assert body["issuer"] == "https://mcp.example.com"
    assert body["registration_endpoint"] == "https://mcp.example.com/oauth/register"
    assert body["authorization_endpoint"] == "https://mcp.example.com/authorize"
    assert body["token_endpoint"] == "https://mcp.example.com/token"
    assert body["response_types_supported"] == ["code"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["scopes_supported"] == ["openid", "email", "profile"]
    assert body["token_endpoint_auth_methods_supported"][0] == "none"
    assert "revocation_endpoint" not in body


def test_build_proxy_advertises_the_refresh_token_grant():
    """Left out, RFC 8414 means authorization_code + implicit: no refresh."""
    body = oauth_as_metadata.build_proxy_authorization_server_metadata(
        mcp_issuer="https://mcp.example.com",
        cognito_oidc={
            "authorization_endpoint": "https://auth.example.com/oauth2/authorize",
            "token_endpoint": "https://auth.example.com/oauth2/token",
            # Cognito's own document lists implicit and client_credentials too;
            # this client is a public PKCE client, so those are not advertised.
            "grant_types_supported": ["authorization_code", "implicit", "client_credentials"],
        },
    )
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]


def test_build_proxy_includes_revocation_when_present():
    body = oauth_as_metadata.build_proxy_authorization_server_metadata(
        mcp_issuer="https://mcp.example.com/",
        cognito_oidc={
            "authorization_endpoint": "https://auth.example.com/oauth2/authorize",
            "token_endpoint": "https://auth.example.com/oauth2/token",
            "revocation_endpoint": "https://auth.example.com/oauth2/revoke",
        },
    )
    assert body["issuer"] == "https://mcp.example.com"
    assert body["revocation_endpoint"] == "https://auth.example.com/oauth2/revoke"


def test_build_proxy_raises_when_endpoints_missing():
    with pytest.raises(oauth_as_metadata.OidcFetchError, match="missing"):
        oauth_as_metadata.build_proxy_authorization_server_metadata(
            mcp_issuer="https://mcp.example.com",
            cognito_oidc={},
        )
