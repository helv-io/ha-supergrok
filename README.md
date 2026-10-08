<p align="center"><img src="logo.svg" width="72" alt="SuperGrok OAuth logo"></p>
<h1 align="center">SuperGrok OAuth</h1>

<p align="center">Use your SuperGrok or X Premium+ subscription in Home Assistant: Assist chat, voice and images. No API key.</p>

<p align="center">Unofficial. Not affiliated with xAI or Home Assistant. xAI can change sign-in and models at any time.</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square" alt="HACS Custom"></a>
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-2026.8+-18bcf2?style=flat-square&logo=home-assistant&logoColor=white" alt="Home Assistant 2026.8+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License"></a>
</p>

## Quick start

You need Home Assistant 2026.8 or newer and a SuperGrok or X Premium+ subscription.

<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=helv-io&repository=ha-supergrok&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open this repository in HACS"></a>
<a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=supergrok"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Add SuperGrok OAuth to Home Assistant"></a>

1. Open this repository in HACS, download **SuperGrok OAuth**, restart Home Assistant.
2. **Settings** > **Devices & services** > **Add integration** > **SuperGrok OAuth**.
3. Open the address shown on any device and approve. Home Assistant continues on its own.
4. Pick the chat models, Voice and Imagine to add.
5. **Settings** > **Voice assistants**: pick Grok Voice STT, a Grok conversation agent and Grok Voice TTS.

Manual install: copy `custom_components/supergrok` to `/config/custom_components/supergrok`, then restart.

To upgrade, update in HACS and restart.

## Features

| Feature | What it does |
| --- | --- |
| Sign-in | Device code, or browser sign-in with a pasted address. Tokens refresh on their own |
| Conversation | Assist agents, each with its own name, instructions, model and Control Home Assistant setting. Replies stream |
| AI Task | `ai_task.generate_data`, plain or structured, with image attachments |
| Imagine | `ai_task.generate_image` |
| Voice | Speech-to-text and text-to-speech for Assist. Voices load from xAI |
| Actions | `supergrok.generate_content` and `supergrok.generate_image` |

## Conversation agents

Open the integration > **Add conversation agent**. An agent has a name, instructions, Control Home Assistant, a chat model, maximum tokens and temperature. **Reconfigure** changes any of them.

**Configure** on the integration changes which models, Voice and Imagine are added.

## Actions

<details><summary>supergrok.generate_content</summary>

```yaml
action: supergrok.generate_content
data:
  config_entry: <your SuperGrok OAuth entry>
  prompt: Describe this snapshot in one sentence.
  image_filename:
    - /config/www/snapshot.jpg
response_variable: reply
```

Returns `text` and `model`.

Images must be JPEG, PNG, WebP or GIF files in `/config/www`, your media folder or a folder listed in [`allowlist_external_dirs`](https://www.home-assistant.io/integrations/homeassistant/#allowlist_external_dirs).

</details>

<details><summary>supergrok.generate_image</summary>

```yaml
action: supergrok.generate_image
data:
  config_entry: <your SuperGrok OAuth entry>
  prompt: A lighthouse at dusk
  aspect_ratio: "16:9"
  resolution: 2k
response_variable: result
```

Returns `images` and `model`. Each image has `url` or `b64_json`, and `revised_prompt`.

</details>

## Sign-in

Device code is the default.

Browser sign-in is the backup. After you approve, the browser opens `http://127.0.0.1:56121/callback` and cannot connect. That is expected: copy the whole address from the address bar and paste it into the form.

An xAI API key does not work here. Sign in with the account that has the subscription.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| Sign-in fails, or chat or Voice answers 402 or 403 | The account needs SuperGrok or X Premium+. If it has one, turn on debug logging and open an issue with the log. |
| The browser cannot reach `127.0.0.1` after sign-in | Expected. Copy the whole address and paste it into the form. |
| `generate_content` says Home Assistant may not read a file | Move the file to `/config/www`, or add its folder to `allowlist_external_dirs` in `configuration.yaml` and restart. |
| "This SuperGrok account is already configured" | The account is already added. Remove that entry first to add it again. |

Debug logging:

```yaml
logger:
  logs:
    custom_components.supergrok: debug
```

<details><summary>Upgrading from 0.5.x</summary>

0.6 renamed the domain from `grok_oauth` to `supergrok`. Old entries do not move over.

1. Update **SuperGrok OAuth** in HACS.
2. Remove the old `grok_oauth` entry if it is still listed.
3. Delete `/config/custom_components/grok_oauth` if it is still there.
4. Restart Home Assistant.
5. Add **SuperGrok OAuth** and sign in again. Pick the Grok entities in your voice assistants again.

</details>

## Built with SI assistance

SuperGrok OAuth is built with SI assistance: coding agents write much of the code, tests and docs.

The author reviews the changes and runs the integration.

## Development

```sh
pip install -r requirements_test.txt
ruff check custom_components tests && pytest -q
```

Release checklist: [TESTING.md](TESTING.md). Contributors and agents: [AGENT.md](AGENT.md).

## License

MIT, see [LICENSE](LICENSE).
