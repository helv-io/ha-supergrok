# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The integration version is `custom_components/grok_oauth/manifest.json` → `version`.

## [0.2.2] - 2026-08-14

### Changed

- Device code is now the default SuperGrok sign-in. Browser / paste-the-localhost-callback is the backup.

## [0.2.1] - 2026-08-13

### Fixed

- Browser SuperGrok login no longer sends My Home Assistant as `redirect_uri` (xAI rejects it). Login uses the registered Grok CLI loopback `http://127.0.0.1:56121/callback` and a paste-the-callback step.

## [0.2.0] - 2026-08-13

### Added

- Initial public integration: SuperGrok OAuth, model picker, conversation, Voice TTS/STT, Realtime, and Imagine.
