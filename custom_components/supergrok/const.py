"""Constants for SuperGrok OAuth."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "supergrok"
LOGGER = logging.getLogger(__package__)
# Settings → System → Logs → custom_components.supergrok = debug

DEFAULT_NAME: Final = "SuperGrok OAuth"
DEFAULT_CONVERSATION_NAME: Final = "Grok Conversation"
DEFAULT_AI_TASK_NAME: Final = "Grok AI Task"
DEFAULT_TTS_NAME: Final = "Grok Voice TTS"
DEFAULT_STT_NAME: Final = "Grok Voice STT"
DEFAULT_REALTIME_NAME: Final = "Grok Realtime"

# Realtime is withheld from this release. Flip to True to restore the picker
# option, conversation entity, and create_realtime_session service. Keep the
# client/helpers in the tree either way.
REALTIME_ENABLED: Final = False

# SuperGrok / Grok CLI public OAuth client (no secret). Same client used by
# the official Grok CLI, Hermes, OpenCode, and other SuperGrok OAuth clients.
OAUTH_CLIENT_ID: Final = "b1a00492-073a-47ea-816f-4c329264a828"
OAUTH_ISSUER: Final = "https://auth.x.ai"
OAUTH_AUTHORIZE_URL: Final = "https://auth.x.ai/oauth2/authorize"
OAUTH_DEVICE_URL: Final = "https://auth.x.ai/oauth2/device/code"
OAUTH_TOKEN_URL: Final = "https://auth.x.ai/oauth2/token"
# Only redirect registered on the public Grok CLI client. My Home Assistant
# (https://my.home-assistant.io/redirect/oauth) is rejected by auth.x.ai.
OAUTH_REDIRECT_URI: Final = "http://127.0.0.1:56121/callback"
OAUTH_USERINFO_URL: Final = "https://auth.x.ai/oauth2/userinfo"
OAUTH_REVOKE_URL: Final = "https://auth.x.ai/oauth2/revoke"
OAUTH_SCOPE: Final = (
    "openid profile email offline_access "
    "grok-cli:access api:access "
    "conversations:read conversations:write"
)
OAUTH_DEVICE_GRANT: Final = "urn:ietf:params:oauth:grant-type:device_code"

# Subscription OAuth traffic belongs on the Grok CLI proxy. The public
# developer API (api.x.ai) often 402/403s a SuperGrok bearer. Voice,
# Imagine, and chat all try the proxy first.
CHAT_API_BASES: Final = (
    "https://cli-chat-proxy.grok.com/v1",
    "https://api.x.ai/v1",
)
MEDIA_API_BASES: Final = (
    "https://cli-chat-proxy.grok.com/v1",
    "https://api.x.ai/v1",
)
REALTIME_WS_URLS: Final = (
    "wss://cli-chat-proxy.grok.com/v1/realtime",
    "wss://api.x.ai/v1/realtime",
)

# grok.com still gates on the CLI auth header. Identify as this integration
# first; fall back to grok-shell if that host 401/403s.
GROK_CLI_TOKEN_AUTH: Final = "xai-grok-cli"
CLIENT_IDENTIFIER: Final = "ha-supergrok"
CLIENT_IDENTIFIER_FALLBACK: Final = "grok-shell"
CLIENT_VERSION_FALLBACK: Final = "0.2.93"
GROK_CLI_HEADERS: Final = {
    "x-xai-token-auth": GROK_CLI_TOKEN_AUTH,
    "x-grok-client-identifier": CLIENT_IDENTIFIER,
    "x-grok-client-version": CLIENT_VERSION_FALLBACK,
}

CONF_ACCESS_TOKEN: Final = "access_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_EXPIRES_AT: Final = "expires_at"
CONF_ID_TOKEN: Final = "id_token"
CONF_ACCOUNT_ID: Final = "account_id"
CONF_ACCOUNT_EMAIL: Final = "account_email"
CONF_ACCOUNT_NAME: Final = "account_name"
CONF_SELECTED_MODELS: Final = "selected_models"
CONF_CHAT_MODEL: Final = "chat_model"
CONF_IMAGE_MODEL: Final = "image_model"
CONF_VOICE_MODEL: Final = "voice_model"
CONF_REALTIME_MODEL: Final = "realtime_model"
CONF_TTS_VOICE: Final = "tts_voice"
CONF_LLM_CONTROL: Final = "llm_control"
CONF_TEMPERATURE: Final = "temperature"
CONF_MAX_TOKENS: Final = "max_tokens"

SUBENTRY_TYPE_CONVERSATION: Final = "conversation"
SUBENTRY_TYPE_AI_TASK: Final = "ai_task_data"

MODEL_VOICE: Final = "voice"
MODEL_REALTIME: Final = "realtime"

DEFAULT_CHAT_MODEL: Final = "grok-4.6"
DEFAULT_IMAGE_MODEL: Final = "grok-imagine-image-2.0"
DEFAULT_VOICE_MODEL: Final = "grok-voice-think-fast-2.0"
DEFAULT_REALTIME_MODEL: Final = "grok-voice-latest"
DEFAULT_TTS_VOICE: Final = "eve"
DEFAULT_MAX_TOKENS: Final = 4096
DEFAULT_TEMPERATURE: Final = 1.0
RETRY_STATUS: Final = (401, 402, 403, 429)
MAX_STATUS_RETRIES: Final = 2

# HA Assist is text in / text out. These xAI voice endpoints map to HA
# platforms; SIP / phone / custom-voice admin do not.
HA_AUDIO_ENDPOINTS: Final = {
    "tts": "POST /v1/tts",
    "tts_voices": "GET /v1/tts/voices",
    "tts_stream": "WSS /v1/tts",
    "stt": "POST /v1/stt",
    "stt_stream": "WSS /v1/stt",
    "realtime": "WSS /v1/realtime",
    "realtime_secrets": "POST /v1/realtime/client_secrets",
}

IMAGE_PATHS: Final = ("/images/generations", "/image/generations")

TOKEN_EXPIRY_SKEW_SECONDS: Final = 120
MAX_TOOL_ITERATIONS: Final = 10

SERVICE_GENERATE_IMAGE: Final = "generate_image"
SERVICE_GENERATE_CONTENT: Final = "generate_content"
SERVICE_CREATE_REALTIME_SESSION: Final = "create_realtime_session"

TTS_CODECS: Final = ("mp3", "wav", "pcm")
TTS_SAMPLE_RATES: Final = (8000, 16000, 22050, 24000, 44100, 48000)

# xAI TTS language codes from the Voice REST reference, expanded to the
# BCP-47 tags Home Assistant Voice Assistants expect.
TTS_LANGUAGE_MAP: Final = {
    "en": "en",
    "en-US": "en",
    "en-GB": "en",
    "en-AU": "en",
    "ar": "ar-SA",
    "ar-SA": "ar-SA",
    "ar-EG": "ar-EG",
    "ar-AE": "ar-AE",
    "bn": "bn",
    "zh": "zh",
    "zh-CN": "zh",
    "zh-TW": "zh",
    "fr": "fr",
    "fr-FR": "fr",
    "de": "de",
    "de-DE": "de",
    "hi": "hi",
    "hi-IN": "hi",
    "id": "id",
    "id-ID": "id",
    "it": "it",
    "it-IT": "it",
    "ja": "ja",
    "ja-JP": "ja",
    "ko": "ko",
    "ko-KR": "ko",
    "pt": "pt-BR",
    "pt-BR": "pt-BR",
    "pt-PT": "pt-PT",
    "ru": "ru",
    "ru-RU": "ru",
    "es": "es-ES",
    "es-ES": "es-ES",
    "es-MX": "es-MX",
    "tr": "tr",
    "tr-TR": "tr",
    "vi": "vi",
    "vi-VN": "vi",
}

TTS_HA_LANGUAGES: Final = [
    "en-US",
    "en-GB",
    "ar-SA",
    "ar-EG",
    "ar-AE",
    "bn",
    "zh-CN",
    "fr-FR",
    "de-DE",
    "hi-IN",
    "id-ID",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "pt-BR",
    "pt-PT",
    "ru-RU",
    "es-ES",
    "es-MX",
    "tr-TR",
    "vi-VN",
]

FALLBACK_TTS_VOICES: Final = (
    ("eve", "Eve"),
    ("ara", "Ara"),
    ("leo", "Leo"),
    ("rex", "Rex"),
    ("sal", "Sal"),
    ("luna", "Luna"),
    ("orion", "Orion"),
    ("iris", "Iris"),
    ("atlas", "Atlas"),
    ("helix", "Helix"),
    ("celeste", "Celeste"),
    ("sirius", "Sirius"),
)

STT_HA_LANGUAGES: Final = TTS_HA_LANGUAGES
