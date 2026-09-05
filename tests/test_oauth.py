"""OAuth helper parsing and public-contract checks. No live xAI."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from custom_components.supergrok.const import (
    CHAT_API_BASES,
    DEFAULT_NAME,
    DOMAIN,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    REALTIME_ENABLED,
    SERVICE_CREATE_REALTIME_SESSION,
    SERVICE_GENERATE_CONTENT,
    SERVICE_GENERATE_IMAGE,
)
from custom_components.supergrok.logutil import preview
from custom_components.supergrok.oauth import (
    GrokOAuthError,
    OAuthTokens,
    account_unique_id,
    build_authorize_url,
    generate_pkce,
    jwt_claims,
    parse_authorization_callback,
    tokens_from_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def test_public_contract_is_unchanged() -> None:
    """OAuth client, loopback redirect, proxy order, and service names stay put."""
    assert DOMAIN == "supergrok"
    assert OAUTH_CLIENT_ID == "b1a00492-073a-47ea-816f-4c329264a828"
    assert OAUTH_REDIRECT_URI == "http://127.0.0.1:56121/callback"
    assert CHAT_API_BASES == (
        "https://cli-chat-proxy.grok.com/v1",
        "https://api.x.ai/v1",
    )
    assert SERVICE_GENERATE_CONTENT == "generate_content"
    assert SERVICE_GENERATE_IMAGE == "generate_image"
    assert SERVICE_CREATE_REALTIME_SESSION == "create_realtime_session"
    assert REALTIME_ENABLED is False
    assert DEFAULT_NAME == "SuperGrok OAuth"


def test_public_name_and_github_urls() -> None:
    """Display name and GitHub URLs follow the ha-supergrok rename."""
    integration = ROOT / "custom_components" / "supergrok"
    manifest = json.loads((integration / "manifest.json").read_text(encoding="utf-8"))
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    services = (integration / "services.yaml").read_text(encoding="utf-8")

    assert not (ROOT / "custom_components" / "grok_oauth").exists()
    assert integration.is_dir()
    assert manifest["domain"] == "supergrok"
    assert manifest["name"] == "SuperGrok OAuth"
    assert manifest["version"] == "0.6.8"
    assert "voluptuous-openapi==0.4.1" in manifest["requirements"]
    assert "ai_task" in manifest["dependencies"]
    assert manifest["loggers"] == ["custom_components.supergrok"]
    assert manifest["documentation"] == "https://github.com/helv-io/ha-supergrok"
    assert manifest["issue_tracker"] == "https://github.com/helv-io/ha-supergrok/issues"
    assert re.match(r"^\d+\.\d+\.\d+$", manifest["version"])
    assert hacs["name"] == "SuperGrok OAuth"
    assert hacs.get("content_in_root") is not True
    assert hacs.get("zip_release") is True
    assert hacs["filename"] == "supergrok.zip"
    assert "ha-grok-oauth" not in readme
    assert "repository=ha-supergrok" in readme
    assert "domain=supergrok" in readme
    assert "<h1 align=\"center\">SuperGrok OAuth</h1>" in readme
    assert "integration: grok_oauth" not in services
    assert "integration: supergrok" in services


def test_hacs_release_zip_has_manifest_at_root(tmp_path: Path) -> None:
    """HACS zip_release extracts into custom_components/supergrok; files stay at zip root."""
    integration = ROOT / "custom_components" / "supergrok"
    dest = tmp_path / "supergrok.zip"
    with zipfile.ZipFile(dest, "w") as archive:
        for path in integration.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(integration).as_posix())
    names = zipfile.ZipFile(dest).namelist()
    assert "manifest.json" in names
    assert not any(name.startswith("custom_components/") for name in names)


def test_platforms_have_modules() -> None:
    """Forwarded platforms must exist as modules so setup does not raise Platform not found."""
    integration = ROOT / "custom_components" / "supergrok"
    init = (integration / "__init__.py").read_text(encoding="utf-8")
    assert "Platform.AI_TASK" in init
    for name in ("ai_task", "conversation", "stt", "tts"):
        assert (integration / f"{name}.py").is_file(), f"missing platform {name}"


def test_parse_full_callback_url() -> None:
    """A pasted localhost callback URL yields code and state."""
    code, state, error = parse_authorization_callback(
        "http://127.0.0.1:56121/callback?code=abc123&state=xyz789"
    )
    assert code == "abc123"
    assert state == "xyz789"
    assert error is None


def test_parse_https_callback_url() -> None:
    """https callbacks are accepted the same way."""
    code, state, error = parse_authorization_callback(
        "https://127.0.0.1:56121/callback?code=from-https&state=st"
    )
    assert code == "from-https"
    assert state == "st"
    assert error is None


def test_parse_bare_authorization_code() -> None:
    """A bare code (no query) is treated as the authorization code."""
    code, state, error = parse_authorization_callback("  just-the-code  ")
    assert code == "just-the-code"
    assert state is None
    assert error is None


def test_parse_query_string_without_scheme() -> None:
    """A pasted query string still extracts code and state."""
    code, state, error = parse_authorization_callback("code=qcode&state=qstate")
    assert code == "qcode"
    assert state == "qstate"
    assert error is None


def test_parse_callback_with_fragment() -> None:
    """Fragment-style callbacks are parsed when there is no query."""
    code, state, error = parse_authorization_callback(
        "http://127.0.0.1:56121/callback#code=frag&state=fs"
    )
    assert code == "frag"
    assert state == "fs"
    assert error is None


def test_parse_empty_and_whitespace() -> None:
    """Empty paste is missing everything."""
    assert parse_authorization_callback("") == (None, None, None)
    assert parse_authorization_callback("   ") == (None, None, None)


def test_parse_access_denied_error() -> None:
    """xAI error query values are returned as the error slot."""
    code, state, error = parse_authorization_callback(
        "http://127.0.0.1:56121/callback?error=access_denied&state=st"
    )
    assert code is None
    assert state == "st"
    assert error == "access_denied"


def test_parse_url_without_code() -> None:
    """A loopback URL with no code or error yields no code."""
    code, state, error = parse_authorization_callback(
        "http://127.0.0.1:56121/callback"
    )
    assert code is None
    assert state is None
    assert error is None


def test_build_authorize_url_uses_cli_loopback() -> None:
    """Browser login must use the registered Grok CLI loopback, not My HA."""
    url = build_authorize_url(code_challenge="challenge", state="state-1")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.x.ai"
    assert params["client_id"] == [OAUTH_CLIENT_ID]
    assert params["redirect_uri"] == [OAUTH_REDIRECT_URI]
    assert params["code_challenge"] == ["challenge"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == ["state-1"]
    assert "my.home-assistant.io" not in url


def test_generate_pkce_s256() -> None:
    """PKCE verifier and challenge are the expected lengths."""
    verifier, challenge = generate_pkce()
    assert 43 <= len(verifier) <= 128
    assert challenge
    assert verifier != challenge


def test_tokens_from_payload_requires_refresh() -> None:
    """A token body without a refresh token is an OAuth error."""
    try:
        tokens_from_payload({"access_token": "only-access"})
    except GrokOAuthError as err:
        assert err.reason == "oauth_error"
    else:
        raise AssertionError("expected GrokOAuthError")


def test_account_unique_id_never_uses_token_prefix() -> None:
    """Unique id is sub/email, never a slice of the access token."""
    assert account_unique_id({"sub": "acct-9", "email": "a@b.c"}) == "acct-9"
    assert account_unique_id({"email": "a@b.c"}) == "a@b.c"
    tokens = OAuthTokens(access_token="not-a-jwt-prefix", refresh_token="r", expires_at=1)
    assert account_unique_id({}, tokens) is None

    # header.payload.sig with {"sub":"from-jwt"}
    payload = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiJmcm9tLWp3dCJ9."
        "x"
    )
    jwt_tokens = OAuthTokens(access_token=payload, refresh_token="r", expires_at=1)
    assert jwt_claims(payload).get("sub") == "from-jwt"
    assert account_unique_id({}, jwt_tokens) == "from-jwt"


def test_oauth_tokens_roundtrip_and_expiry() -> None:
    """Config-entry token dicts load and report expiry."""
    tokens = OAuthTokens(
        access_token="access",
        refresh_token="refresh",
        expires_at=1,
    )
    loaded = OAuthTokens.from_dict(tokens.as_dict())
    assert loaded.access_token == "access"
    assert loaded.refresh_token == "refresh"
    assert loaded.is_expired()


def test_preview_redacts_tokens() -> None:
    """Log previews must never include bearer tokens or JWTs."""
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "signaturepartthatislongenoughxx"
    )
    text = preview(f"Authorization: Bearer {jwt} leftover")
    assert jwt not in text
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in text
    assert "signaturepartthatislongenoughxx" not in text
