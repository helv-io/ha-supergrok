# Changelog

All notable changes to SuperGrok OAuth are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version is `version` in `custom_components/supergrok/manifest.json`. A release tag copies its section here into the GitHub Release.

## [Unreleased]

## [0.7.0] - 2026-10-14

### Added

- **Grok Latest** model choice that follows new Grok releases, now the default

### Changed

- Agents still on the old default, Grok 4.6, move to **Grok Latest** on update
- Model pickers list the models your account has each time they open, so new releases show up without an update

## [0.6.11] - 2026-10-08

### Fixed

- Signing in again works on Home Assistant 2026.12 and later, and reloads the integration once

## [0.6.10] - 2026-10-08

### Fixed

- Token refreshes no longer reload the integration, so chats and voice replies in progress keep going
- Changing models in **Configure** reloads once instead of twice

### Security

- `generate_content` only reads images from folders in `allowlist_external_dirs`, and only JPEG, PNG, WebP or GIF files

## [0.6.9] - 2026-10-08

### Changed

- README, setup screens and action descriptions rewritten in plain language
- Logs no longer include the account name or email

### Fixed

- The nightly sign-in check works

## [0.6.8] - 2026-09-05

### Fixed

- MCP tools with number ids get a number, not a string, even when the MCP server describes the id loosely

## [0.6.7] - 2026-09-05

### Fixed

- Tool calls that send a number or true/false as text are turned into the type the tool expects before the call

## [0.6.6] - 2026-09-05

### Fixed

- Assist no longer fails with `TypeError: cannot use 'list' as a dict key` when an MCP tool has list or union fields

## [0.6.5] - 2026-09-05

### Fixed

- MCP tools with required fields work. Grok was only allowed to send `{}`, so calls such as creating an entry failed on a missing field
- A tool call that still misses a required field is answered with the missing names instead of being sent

## [0.6.4] - 2026-08-27

### Fixed

- Text-to-speech always sends a language. Voice satellites stayed silent when xAI answered 422
- Speech-to-text always sends a language with formatting on, and falls back to English when the Assist language has none

## [0.6.3] - 2026-08-27

### Fixed

- Speech-to-text no longer fails with `format requires language`

## [0.6.2] - 2026-08-27

### Fixed

- Setup no longer fails with `No module named 'voluptuous_openapi'`

## [0.6.1] - 2026-08-27

Reconfiguration required when coming from 0.5.x. See Upgrading from 0.5.x in the [README](https://github.com/helv-io/ha-supergrok#troubleshooting).

### Fixed

- The GitHub Release includes `supergrok.zip`, so HACS can download it

## [0.6.0] - 2026-08-26

Reconfiguration required. Old `grok_oauth` entries do not move over.

1. Update SuperGrok OAuth in HACS.
2. Remove the old `grok_oauth` entry if it is still listed.
3. Delete `/config/custom_components/grok_oauth` if it is still there.
4. Restart Home Assistant.
5. Add **SuperGrok OAuth** and sign in again. Pick the Grok entities in your voice assistants again.

### Breaking

- Domain renamed from `grok_oauth` to `supergrok`
- Actions are `supergrok.generate_content` and `supergrok.generate_image`
- The debug logger is `custom_components.supergrok`

### Fixed

- Setup no longer fails with `Platform grok_oauth.ai_task not found`

## [0.5.0] - 2026-08-26

### Added

- Conversation replies stream into Assist
- Image attachments on conversation and AI Task, and `image_filename` on `generate_content`
- Temperature and maximum tokens on conversation agents and AI Task
- Structured `generate_data` accepts JSON wrapped in markdown
- Removing the integration signs out at xAI
- Models from your account show in the chat and Imagine pickers
- Bug report template

### Fixed

- Speech-to-text works on subscription accounts
- Speech-to-text accepts 24 kHz audio
- Rate limits are retried with backoff
- Text-to-speech no longer sends each request twice
- Device code sign-in works when the profile lookup fails
- Voice-only setups no longer get a conversation agent
- The tool-call limit still ends with a reply
- Diagnostics hide the account email and name

### Changed

- Voice and Imagine use the same host as chat
- Conversation and AI Task entities follow Home Assistant entity naming. Entity ids stay
- README says the integration is unofficial

## [0.4.0] - 2026-08-16

### Added

- Conversation agents and AI Task are added from the integration page. Each agent has a name, instructions, Control Home Assistant and a chat model. Existing agents move over and keep their instructions

## [0.3.2] - 2026-08-14

### Changed

- README and setup text: no em dashes. My Home Assistant buttons fixed

## [0.3.1] - 2026-08-14

### Changed

- Renamed to SuperGrok OAuth, repository `helv-io/ha-supergrok`. The domain stays `grok_oauth`

## [0.3.0] - 2026-08-14

### Removed

- Realtime, for now

## [0.2.2] - 2026-08-14

### Changed

- Device code is the default sign-in. Browser sign-in is the backup

## [0.2.1] - 2026-08-13

### Fixed

- Browser sign-in works. xAI only accepts the Grok CLI address, so you paste it back after approving

## [0.2.0] - 2026-08-13

### Added

- First release: SuperGrok sign-in, model picker, conversation, Voice and Imagine
