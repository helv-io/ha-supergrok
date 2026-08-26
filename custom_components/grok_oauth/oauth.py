"""SuperGrok OAuth against auth.x.ai (device code + auth-code PKCE)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from .const import (
    LOGGER,
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_DEVICE_GRANT,
    OAUTH_DEVICE_URL,
    OAUTH_REDIRECT_URI,
    OAUTH_REVOKE_URL,
    OAUTH_SCOPE,
    OAUTH_TOKEN_URL,
    OAUTH_USERINFO_URL,
    TOKEN_EXPIRY_SKEW_SECONDS,
)


class GrokOAuthError(Exception):
    """OAuth failure with a stable reason code."""

    def __init__(self, reason: str, details: str = "") -> None:
        super().__init__(details or reason)
        self.reason = reason
        self.details = details


@dataclass(slots=True)
class DeviceAuthorization:
    """RFC 8628 device-code challenge."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass(slots=True)
class OAuthTokens:
    """Access + rotating refresh token."""

    access_token: str
    refresh_token: str
    expires_at: int
    id_token: str | None = None
    token_type: str = "Bearer"

    def as_dict(self) -> dict[str, Any]:
        """Serialize for a config entry."""
        data: dict[str, Any] = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }
        if self.id_token:
            data["id_token"] = self.id_token
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthTokens:
        """Load tokens from config entry data."""
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=int(data.get("expires_at") or 0),
            id_token=data.get("id_token"),
            token_type=data.get("token_type", "Bearer"),
        )

    def is_expired(self, skew: int = TOKEN_EXPIRY_SKEW_SECONDS) -> bool:
        """Return True if the access token should be refreshed."""
        if not self.expires_at:
            return True
        return time.time() >= (self.expires_at - skew)


def jwt_claims(token: str | None) -> dict[str, Any]:
    """Decode an unsigned JWT payload. Returns {} if the token is not a JWT."""
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def account_unique_id(
    account: dict[str, Any], tokens: OAuthTokens | dict[str, Any] | None = None
) -> str | None:
    """Stable account id: userinfo sub, then JWT sub/email, then email.

    Never use a token prefix. A missing id means setup should abort.
    """
    if sub := account.get("sub"):
        return str(sub)
    token_data: dict[str, Any] = {}
    if isinstance(tokens, OAuthTokens):
        token_data = tokens.as_dict()
    elif isinstance(tokens, dict):
        token_data = tokens
    for candidate in (token_data.get("id_token"), token_data.get("access_token")):
        claims = jwt_claims(candidate if isinstance(candidate, str) else None)
        if sub := claims.get("sub"):
            return str(sub)
        if email := claims.get("email"):
            return str(email)
    if email := account.get("email"):
        return str(email)
    return None


def _expires_at_from_payload(payload: dict[str, Any]) -> int:
    """Compute a unix expiry, preferring expires_in then JWT exp."""
    if expires_in := payload.get("expires_in"):
        try:
            return int(time.time()) + int(expires_in)
        except (TypeError, ValueError):
            pass
    claims = jwt_claims(payload.get("access_token") or "")
    if exp := claims.get("exp"):
        try:
            return int(exp)
        except (TypeError, ValueError):
            pass
    return int(time.time()) + 900


def tokens_from_payload(payload: dict[str, Any], previous: OAuthTokens | None = None) -> OAuthTokens:
    """Parse a token endpoint JSON body."""
    access = payload.get("access_token")
    refresh = payload.get("refresh_token") or (previous.refresh_token if previous else None)
    if not access or not refresh:
        raise GrokOAuthError("oauth_error", "Token response missing access or refresh token")
    return OAuthTokens(
        access_token=access,
        refresh_token=refresh,
        expires_at=_expires_at_from_payload(payload),
        id_token=payload.get("id_token") or (previous.id_token if previous else None),
        token_type=payload.get("token_type") or "Bearer",
    )


def generate_pkce(length: int = 128) -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    length = min(max(length, 43), 128)
    verifier = secrets.token_urlsafe(96)[:length]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return verifier, challenge


def build_authorize_url(*, code_challenge: str, state: str) -> str:
    """Authorization-code URL using the registered Grok CLI loopback redirect."""
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode({
        'response_type': 'code',
        'client_id': OAUTH_CLIENT_ID,
        'redirect_uri': OAUTH_REDIRECT_URI,
        'scope': OAUTH_SCOPE,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    })}"


def parse_authorization_callback(value: str) -> tuple[str | None, str | None, str | None]:
    """Parse a pasted callback URL, query string, or bare code.

    Returns (code, state, error).
    """
    raw = (value or "").strip()
    if not raw:
        return None, None, None

    query = ""
    lowered = raw.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        parsed = urlparse(raw)
        query = parsed.query or parsed.fragment
    elif "code=" in raw or "error=" in raw:
        query = raw.split("?", 1)[-1]
    else:
        return raw, None, None

    params = parse_qs(query, keep_blank_values=False)
    code = (params.get("code") or [None])[0]
    state = (params.get("state") or [None])[0]
    error = (params.get("error") or [None])[0]
    return code, state, error


async def exchange_authorization_code(
    session: aiohttp.ClientSession,
    code: str,
    code_verifier: str,
) -> OAuthTokens:
    """Exchange an authorization code + PKCE verifier for tokens."""
    async with session.post(
        OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": OAUTH_CLIENT_ID,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": OAUTH_REDIRECT_URI,
        },
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        text = await resp.text()
        try:
            payload = await resp.json(content_type=None)
        except aiohttp.ContentTypeError:
            payload = {"error": text}

    if resp.status >= 400:
        error = payload.get("error") or f"http_{resp.status}"
        details = str(payload.get("error_description") or error)
        LOGGER.warning("Authorization-code exchange failed (%s): %s", resp.status, details[:200])
        if error in ("access_denied", "authorization_denied"):
            raise GrokOAuthError("access_denied", details)
        if resp.status == 403:
            raise GrokOAuthError("tier_blocked", details)
        raise GrokOAuthError("oauth_failed", details)
    LOGGER.info("SuperGrok browser login succeeded")
    return tokens_from_payload(payload)


async def request_device_authorization(session: aiohttp.ClientSession) -> DeviceAuthorization:
    """Start an RFC 8628 device-code login."""
    async with session.post(
        OAUTH_DEVICE_URL,
        data={"client_id": OAUTH_CLIENT_ID, "scope": OAUTH_SCOPE},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        text = await resp.text()
        if resp.status >= 400:
            LOGGER.warning("Device authorization failed (%s): %s", resp.status, text[:300])
            raise GrokOAuthError("cannot_connect", text[:200])
        try:
            payload = await resp.json(content_type=None)
        except aiohttp.ContentTypeError as err:
            raise GrokOAuthError("cannot_connect", text[:200]) from err

    device_code = payload.get("device_code")
    user_code = payload.get("user_code")
    verification_uri = payload.get("verification_uri") or payload.get("verification_url")
    if not device_code or not user_code or not verification_uri:
        raise GrokOAuthError("oauth_error", "Device authorization response incomplete")
    complete = payload.get("verification_uri_complete") or verification_uri
    return DeviceAuthorization(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=complete,
        expires_in=int(payload.get("expires_in") or 900),
        interval=max(int(payload.get("interval") or 5), 1),
    )


async def poll_device_token(
    session: aiohttp.ClientSession,
    device: DeviceAuthorization,
    *,
    abort: asyncio.Event | None = None,
) -> OAuthTokens:
    """Poll the token endpoint until the user approves or the code expires."""
    interval = device.interval
    deadline = time.time() + device.expires_in
    while time.time() < deadline:
        if abort and abort.is_set():
            raise GrokOAuthError("aborted")
        await asyncio.sleep(interval)
        if abort and abort.is_set():
            raise GrokOAuthError("aborted")
        async with session.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": OAUTH_DEVICE_GRANT,
                "client_id": OAUTH_CLIENT_ID,
                "device_code": device.device_code,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            try:
                payload = await resp.json(content_type=None)
            except aiohttp.ContentTypeError:
                payload = {"error": await resp.text()}

        error = payload.get("error")
        if resp.status < 400 and payload.get("access_token"):
            LOGGER.info("SuperGrok device login succeeded")
            return tokens_from_payload(payload)
        if error in ("authorization_pending", "slow_down"):
            if error == "slow_down":
                interval = min(interval + 5, 30)
            continue
        if error in ("access_denied", "authorization_denied"):
            raise GrokOAuthError("access_denied", str(payload.get("error_description") or error))
        if error == "expired_token":
            raise GrokOAuthError("oauth_timeout", "Device code expired")
        LOGGER.warning("Unexpected device-token response (%s): %s", resp.status, str(payload)[:300])
        raise GrokOAuthError("oauth_failed", str(payload.get("error_description") or error or resp.status))
    raise GrokOAuthError("oauth_timeout", "Timed out waiting for SuperGrok approval")


async def refresh_tokens(
    session: aiohttp.ClientSession,
    tokens: OAuthTokens,
) -> OAuthTokens:
    """Exchange the rotating refresh token for a new pair."""
    async with session.post(
        OAUTH_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": OAUTH_CLIENT_ID,
            "refresh_token": tokens.refresh_token,
        },
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        text = await resp.text()
        try:
            payload = await resp.json(content_type=None)
        except aiohttp.ContentTypeError:
            payload = {"error": text}

    if resp.status == 403:
        raise GrokOAuthError(
            "tier_blocked",
            payload.get("error_description")
            or "This SuperGrok account is not entitled to the OAuth API surface",
        )
    if resp.status >= 400:
        error = payload.get("error") or f"http_{resp.status}"
        if error == "invalid_grant" or resp.status in (400, 401):
            raise GrokOAuthError("reauth_required", str(payload.get("error_description") or error))
        raise GrokOAuthError("oauth_failed", str(payload.get("error_description") or error))
    return tokens_from_payload(payload, previous=tokens)


async def revoke_tokens(session: aiohttp.ClientSession, tokens: OAuthTokens) -> None:
    """Best-effort RFC 7009 revoke of the refresh token (then access token).

    Callers must not fail entry removal if this raises or returns 4xx.
    """
    for token in (tokens.refresh_token, tokens.access_token):
        if not token:
            continue
        try:
            async with session.post(
                OAUTH_REVOKE_URL,
                data={"token": token, "client_id": OAUTH_CLIENT_ID},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status >= 400:
                    LOGGER.warning("SuperGrok token revoke returned %s", resp.status)
                else:
                    LOGGER.debug("SuperGrok token revoked (%s)", resp.status)
                    return
        except (TimeoutError, aiohttp.ClientError) as err:
            LOGGER.warning("SuperGrok token revoke failed: %s", err)
            return


async def fetch_userinfo(
    session: aiohttp.ClientSession,
    access_token: str,
) -> dict[str, Any]:
    """Load the signed-in SuperGrok profile."""
    async with session.get(
        OAUTH_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        if resp.status >= 400:
            LOGGER.debug("userinfo failed (%s)", resp.status)
            return {}
        try:
            data = await resp.json(content_type=None)
        except aiohttp.ContentTypeError:
            return {}
    return data if isinstance(data, dict) else {}
