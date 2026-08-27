# Pre-release checklist for 0.6.3

Use a throwaway Home Assistant 2026.8+ instance and a real SuperGrok or X Premium+ account. Unit tests cannot prove Voice or the CLI proxy.

0.6.x is a domain rename. Do not keep `custom_components/grok_oauth` next to `custom_components/supergrok`. HACS 0.6.3 must download `supergrok.zip` from the GitHub release and must not raise `No module named 'voluptuous_openapi'`.

Install this branch: copy `custom_components/supergrok` into `/config/custom_components/supergrok` and restart. If `/config/custom_components/grok_oauth` exists, delete it first. Do not tag until every box you can run is ticked.

## A. Fresh install and login

- [ ] Integration appears as **SuperGrok OAuth** (domain `supergrok`).
- [ ] README says unofficial / not affiliated.
- [ ] Device code (default): verification URL + user code; approving on another device finishes without paste. Unique id is the account `sub`, not a token prefix.
- [ ] Browser backup: authorize URL uses `http://127.0.0.1:56121/callback` (not my.home-assistant.io). Full `code=` paste works. Wrong `state` is rejected. Bare code still works.
- [ ] Second add of the same account → already configured.
- [ ] Deny on xAI → `access_denied`, no half-created entry.
- [ ] Non-entitled account (if you have one) → `tier_blocked`.

## B. Model picker and subentries

- [ ] Default picker: Grok 4.6 + Voice + Imagine. Realtime is absent.
- [ ] Voice-only: STT + TTS exist; no conversation agent and no AI Task invented.
- [ ] Chat-only: conversation entity, no STT/TTS.
- [ ] Add → Conversation: name, prompt, Control Home Assistant, model. A second agent is independent.
- [ ] Reconfigure a conversation: prompt change applies on the next Assist chat. Entity id unchanged.
- [ ] Add → AI Task: chat + Imagine. Leave Imagine unset → generate_image disabled, generate_data still works.
- [ ] Options: toggling Voice off removes STT/TTS; toggling back recreates them.

## C. Chat / Assist

- [ ] Assist text streams in the UI (not a single dump after several seconds).
- [ ] Control Home Assistant on: “turn on the test light” actually toggles the light.
- [ ] Control off: the model cannot toggle lights.
- [ ] Multi-turn follow-up uses prior context.
- [ ] A prompt that would hammer tools still ends with a reply (no empty Assist bubble).
- [ ] Image attachment (doorbell snapshot) is described. Non-image attachment errors cleanly.
- [ ] Developer tools → `supergrok.generate_content` with a prompt; with `image_filename`; with a bad config entry (validation error).
- [ ] Debug log for `custom_components.supergrok` shows `cli-chat-proxy.grok.com` vs `api.x.ai` and does not print tokens or emails.

## D. Voice

- [ ] Assist pipeline: Grok Voice STT → Grok conversation → Grok Voice TTS.
- [ ] English satellite (16 kHz WAV): transcript is not empty; reply is spoken.
- [ ] Non-English Assist language (for example `pt-BR` or `de-DE`): STT uses that language, not `en`.
- [ ] 24 kHz source (if you have one) is accepted.
- [ ] TTS voice list loads. Switching voice works.
- [ ] If `api.x.ai` 402s, STT/TTS still succeed via grok.com (debug log).
- [ ] Failed STT returns an Assist error, not a hang.

## E. Imagine / AI Task

- [ ] Setup does not raise `Platform supergrok.ai_task not found`. An AI Task entity is created when chat/Imagine is selected.
- [ ] `ai_task.generate_image` or `supergrok.generate_image` returns an image. 1k and 16:9 still work.
- [ ] `ai_task.generate_data` unstructured: plain text.
- [ ] `ai_task.generate_data` with a structure: valid JSON, not a markdown-wrapped failure.
- [ ] No Imagine model on the AI Task: generate_image errors with a clear message.

## F. Auth durability

- [ ] Restart HA: session still works.
- [ ] Diagnostics: no access/refresh/id token, no email.
- [ ] Remove the integration: re-add requires a new login (refresh token revoked). HA does not get stuck if revoke 4xxs.
- [ ] Reauth with the same account succeeds; a different account → `wrong_account`.

## G. Upgrade from 0.5.0

- [ ] Install 0.5.0 (`grok_oauth`), add SuperGrok, create a named conversation agent, point Assist at Grok STT/TTS.
- [ ] Upgrade to this branch (HACS or copy `custom_components/supergrok`). Delete leftover `custom_components/grok_oauth`. Restart.
- [ ] The old `grok_oauth` config entry is dead. Remove it, add SuperGrok OAuth (`supergrok`), sign in again, and re-select Assist entities.

## H. Must still be true

- [ ] Realtime is not in the picker. `supergrok.create_realtime_session` is not registered.
- [ ] Domain is `supergrok`. Services are `supergrok.generate_content` / `generate_image`.
- [ ] HACS download of 0.6.3 succeeds and writes `/config/custom_components/supergrok` (not "no content to download"). Setup does not fail on `voluptuous_openapi`.
- [ ] `pytest` green. Hassfest + HACS Action green.
- [ ] Ruff clean on `custom_components/` and `tests/`.

When this is green: merge to `main`, tag `0.6.3`. The tag creates the GitHub Release from CHANGELOG and attaches `supergrok.zip`. HACS default-store is a follow-up, not this branch.
