<p align="center">
  <img src="logo.svg" alt="SuperGrok" width="96" height="96">
</p>

<h1 align="center">SuperGrok OAuth</h1>

<p align="center">
  SuperGrok in Home Assistant via OAuth. Conversation, Voice, Imagine. No API key. SpaceXAI.
</p>

<p align="center">
  Unofficial community integration. Not affiliated with xAI or Home Assistant. OAuth, the Grok CLI proxy, and available models can change without notice.
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square" alt="HACS Custom"></a>
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-2026.8+-18bcf2?style=flat-square&logo=home-assistant&logoColor=white" alt="Home Assistant 2026.8+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License"></a>
</p>

<p align="center">
<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=helv-io&repository=ha-supergrok&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and open a repository inside the Home Assistant Community Store."></a>
<a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=supergrok"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Open your Home Assistant instance and start setting up a new integration."></a>
</p>

## Upgrading from 0.5.x (reconfiguration required)

0.6.x changes the Home Assistant domain from `grok_oauth` to `supergrok`. Old config entries will not migrate. Use 0.6.4 or later in HACS (0.6.0 had no release zip; 0.6.1 crashed setup without `voluptuous-openapi`).

1. Update **SuperGrok OAuth** to 0.6.4 in HACS (same custom repository: `helv-io/ha-supergrok`).
2. Remove the old integration entry if it is still listed (it is domain `grok_oauth` and is dead).
3. Delete `/config/custom_components/grok_oauth` if that folder is still there. HACS installs the new domain as `/config/custom_components/supergrok` and may leave the old folder behind.
4. Restart Home Assistant.
5. Add **SuperGrok OAuth** and sign in again.

## What you get

- **Chat:** [conversation](https://www.home-assistant.io/integrations/conversation/) and [text generation](https://www.home-assistant.io/actions/ai_task.generate_data/)
- **Voice:** [speech-to-text](https://www.home-assistant.io/integrations/stt/) and [text-to-speech](https://www.home-assistant.io/integrations/tts/)
- **Imagine:** [image generation](https://www.home-assistant.io/actions/ai_task.generate_image/)

## Install

1. Open this repository in HACS (button above).
2. Download **SuperGrok OAuth**.
3. Restart Home Assistant.

Custom repository: HACS → Integrations → Custom repositories → `https://github.com/helv-io/ha-supergrok` → Integration. HACS installs domain `supergrok` from `custom_components/supergrok/`.

Manual: copy `custom_components/supergrok` to `/config/custom_components/supergrok/`.

If you previously had `custom_components/grok_oauth`, delete that folder. Update via HACS, then reconfigure; the old `grok_oauth` entry is dead.

## Add the integration

<p align="center">
<a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=supergrok"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Open your Home Assistant instance and start setting up a new integration."></a>
</p>

Or: Settings → Devices & services → Add integration → SuperGrok OAuth.

### Sign in

Sign in with SuperGrok or X Premium+. No API key.

Device code is the default. Open the verification URL on any device and approve. Home Assistant continues on its own.

Browser login is the backup (paste the localhost callback). After you approve, the browser opens `http://127.0.0.1:56121/callback` and says the site can't be reached. That is expected: copy the full URL from the address bar and paste it into the form.

## Conversation agents

After SuperGrok is set up, open the integration and use **Add → Conversation** to create an agent. Each agent has its own name, system prompt (template), Control Home Assistant setting, and chat model. Reconfigure an agent to edit the prompt.

## Voice

Point [Assist](https://www.home-assistant.io/voice_control/) at Grok Voice STT, a Grok conversation agent, and Grok Voice TTS.

## Requirements

- Home Assistant 2026.8+
- SuperGrok or X Premium+

## Troubleshooting

**Chat or Voice returns 402 / 403**
This integration uses your SuperGrok / X Premium+ subscription via OAuth, not an xAI developer API key. If the account is not entitled, sign-in aborts with a tier error. If chat works but Voice fails, check logs for `cli-chat-proxy.grok.com` vs `api.x.ai`.

**`redirect_uri does not match any registered URI`**
You used My Home Assistant as the callback. Use device code (the default), or browser login and paste the localhost callback.

**Browser says the site can't be reached after sign-in**
Expected. Copy `http://127.0.0.1:56121/callback?code=...` from the address bar and paste it back.

**This SuperGrok account is already configured**
The existing entry is still valid. Remove it first only if you want a clean re-add.

## License

[MIT](LICENSE)
