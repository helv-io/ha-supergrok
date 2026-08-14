<p align="center">
  <img src="custom_components/grok_oauth/brand/logo@2x.png" width="128" alt="Grok OAuth">
</p>

<h1 align="center">Grok OAuth</h1>

<p align="center">
  SuperGrok in Home Assistant — conversation, Voice, and Imagine.<br>
  No <code>XAI_API_KEY</code>. Sign in with your SuperGrok or X Premium+ account.
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square" alt="HACS Custom"></a>
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-2026.8+-18bcf2?style=flat-square&logo=home-assistant&logoColor=white" alt="Home Assistant 2026.8+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License"></a>
  <a href="https://github.com/helv-io/ha-grok-oauth/issues"><img src="https://img.shields.io/github/issues/helv-io/ha-grok-oauth?style=flat-square" alt="Issues"></a>
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=helv-io&repository=ha-grok-oauth&category=integration">
    <img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.">
  </a>
  &nbsp;
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=grok_oauth">
    <img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Open your Home Assistant instance and start setting up a new integration.">
  </a>
</p>

> Unofficial. Uses the same SuperGrok OAuth surface as Grok CLI / Hermes. xAI can change or gate it at any time. Not affiliated with xAI or Home Assistant.

---

## What you get

Pick models once during setup. Each choice becomes a Home Assistant surface:

| You pick | Home Assistant gets |
| --- | --- |
| Chat (`grok-4.6`, `grok-4-fast`, …) | Conversation agents + AI Task |
| **Voice** | TTS (`POST /v1/tts`) and STT (`POST /v1/stt`) |
| Imagine | `ai_task.generate_image` + `grok_oauth.generate_image` |

Change the picker later from the integration options. No YAML.

Realtime is not in this release.

---

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=helv-io&repository=ha-grok-oauth&category=integration)

1. Click the button — Home Assistant opens this repository in HACS.
2. **Download** **Grok OAuth**.
3. Restart Home Assistant.

No HACS yet? Install it from [hacs.xyz](https://hacs.xyz/docs/use/download/download/) first.

**Manual HACS add**

HACS → Integrations → ⋮ → **Custom repositories** → `https://github.com/helv-io/ha-grok-oauth` → category **Integration**.

### Manual

Copy `custom_components/grok_oauth` into your config directory:

```text
/config/custom_components/grok_oauth/
```

Restart Home Assistant.

---

## Add the integration

After the restart:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=grok_oauth)

Or: **Settings → Devices & services → Add integration → Grok OAuth**.

### Sign in

**Device code** is the default. Open the verification URL on any device, approve, and the form continues on its own — no paste.

**Browser login** is the backup. xAI’s SuperGrok / Grok CLI public client only allows

`http://127.0.0.1:56121/callback`

It **rejects** My Home Assistant (`https://my.home-assistant.io/redirect/oauth`). That is why the browser path asks you to paste a URL.

1. Choose **Browser login (backup — paste the localhost callback URL)**.
2. Open the SuperGrok sign-in link and approve access.
3. The browser goes to `http://127.0.0.1:56121/callback?...` and fails to connect. **That is expected** — nothing is listening on your computer.
4. Copy the **full URL** from the address bar (it contains `code=`) and paste it into the form.

Then multi-select chat models, Voice, and Imagine.

---

## Voice Assistant pipeline

Point a [Voice Assistant](https://my.home-assistant.io/redirect/voice_assistants/) at:

1. **Grok Voice STT**
2. A Grok conversation agent
3. **Grok Voice TTS**

---

## Services

| Service | What it does |
| --- | --- |
| `grok_oauth.generate_content` | Chat completion with the SuperGrok session |
| `grok_oauth.generate_image` | Imagine (`/v1/images/generations`) |

Open them from Developer Tools:

[![Open your Home Assistant instance and show your service developer tools.](https://my.home-assistant.io/badges/developer_services.svg)](https://my.home-assistant.io/redirect/developer_services/)

---

## Logging

One-line info logs for conversation, chat, STT, TTS, Imagine, and token refresh. Tokens are never logged.

For request traces, set `custom_components.grok_oauth` to **debug** under **Settings → System → Logs**, or:

```yaml
logger:
  default: info
  logs:
    custom_components.grok_oauth: debug
```

Diagnostics (⋮ on the integration card) include selected models, token expiry, and recent events — no tokens.

---

## Requirements

- Home Assistant **2026.8+**
- SuperGrok or X Premium+

---

## Troubleshooting

**`redirect_uri does not match any registered URI`**  
You hit xAI with My Home Assistant as the callback. Use **device code** (the default), or **0.2.2** or later and the paste-callback browser backup.

**Browser says the site can’t be reached after sign-in**  
Expected. Copy `http://127.0.0.1:56121/callback?code=…` from the address bar and paste it back.

**This SuperGrok account is already configured**  
The existing entry is still valid. Remove it first only if you want a clean re-add.

**Chat works, developer API 402/403s**  
Subscription OAuth traffic goes through the Grok CLI proxy first (`cli-chat-proxy.grok.com`), not `api.x.ai`.

---

## Versioning

This project follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). The integration version is the `version` field in [`custom_components/grok_oauth/manifest.json`](custom_components/grok_oauth/manifest.json).

Given a version `MAJOR.MINOR.PATCH`:

- **MAJOR** — incompatible changes to the integration’s public surface (config entries, entities, services)
- **MINOR** — backwards-compatible features
- **PATCH** — backwards-compatible fixes

`0.y.z` is initial development: the surface can still move. See [CHANGELOG.md](CHANGELOG.md).

## Disclaimer

This project is unofficial and not affiliated with, endorsed by, or supported by xAI or the Home Assistant project. SuperGrok OAuth can change without notice.

## License

[MIT](LICENSE) © 2026 Helvio Pedreschi
