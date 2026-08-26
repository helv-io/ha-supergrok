# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The integration version is `custom_components/grok_oauth/manifest.json` → `version`.

## [Unreleased]

## [0.5.0] - 2026-08-26

### Added

- Conversation replies stream into Assist instead of arriving in one dump.
- Image attachments on conversation / AI Task and optional `image_filename` on `generate_content`.
- Temperature and max-tokens on conversation and AI Task subentries.
- Structured `generate_data` accepts JSON even when Grok wraps it in markdown.
- Token revoke when the integration is removed.
- Live model ids from the account are merged into the chat/Imagine pickers.
- GitHub bug report template and an unauthenticated nightly smoke check against auth.x.ai.

### Fixed

- Voice STT talks to the SuperGrok CLI proxy (not only `api.x.ai`).
- STT accepts 24 kHz audio. Empty-transcript logs no longer print the `preview` function.
- 429 responses retry with backoff instead of failing the request.
- TTS does not POST twice when the gateway already returned audio.
- Device-code login survives a userinfo failure. Unique ids come from `sub` / JWT, never a token prefix.
- Voice-only installs no longer invent a Grok 4.6 conversation agent.
- Hitting the tool-call limit still produces a final assistant reply.
- Diagnostics redact account email and name.

### Changed

- Identify as `ha-supergrok` on grok.com (still send the CLI auth header; fall back to `grok-shell` if that host 401/403s).
- Media (Voice / Imagine) prefers `cli-chat-proxy.grok.com` the same way chat already did.
- README restores an unofficial / not affiliated notice.
- Conversation / AI Task entities use `has_entity_name` (unique ids unchanged). Voice STT/TTS keep an explicit engine name so Assist TTS does not fail with "TTS engine name is not set."
- Voice still advertises Assist languages so Grok can be used on any pipeline, but `/v1/tts` and `/v1/stt` no longer send `language` (xAI auto-detects).
- README uses the official Grok mark (`logo.svg`).

## [0.4.0] - 2026-08-16

### Added

- Conversation and AI Task config subentries (Home Assistant `ConfigSubentryFlow`, same shape as official OpenAI Conversation). After login, Add → Conversation lets you name an agent, edit its system prompt (template), choose Control Home Assistant, and pick a chat model. Existing installs that only have `selected_models` / entry-level `prompt` keep working: they are migrated to subentries (or fall back) so current Grok conversation entities still appear and still use the stored prompt.

### Changed

- README rewritten for Home Assistant consumers (SpaceXAI, official HA building-block links).

## [0.3.2] - 2026-08-14

### Changed

- Drop em dashes from copy (README, setup strings, picker labels) and put each My Home Assistant badge on one line so GitHub does not treat leftover whitespace as a link.

## [0.3.1] - 2026-08-14

### Changed

- Public name and GitHub repository are now SuperGrok OAuth (`helv-io/ha-supergrok`). The Home Assistant domain remains `grok_oauth`.

## [0.3.0] - 2026-08-14

### Changed

- Realtime is withheld pending a later release. Setup and options no longer offer it; existing entries that had it selected no longer expose a Realtime conversation entity.
- HACS Action runs with no `ignore` keys (topics are already set on the GitHub repository).

### Removed

- `grok_oauth.create_realtime_session` is not registered in this release.

## [0.2.2] - 2026-08-14

### Changed

- Device code is now the default SuperGrok sign-in. Browser / paste-the-localhost-callback is the backup.

## [0.2.1] - 2026-08-13

### Fixed

- Browser SuperGrok login no longer sends My Home Assistant as `redirect_uri` (xAI rejects it). Login uses the registered Grok CLI loopback `http://127.0.0.1:56121/callback` and a paste-the-callback step.

## [0.2.0] - 2026-08-13

### Added

- Initial public integration: SuperGrok OAuth, model picker, conversation, Voice TTS/STT, Realtime, and Imagine.
