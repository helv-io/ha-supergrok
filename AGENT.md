# AGENT.md

Guidelines for changes to SuperGrok OAuth. Applies to code, UI text, docs and releases.

## Product principle

SuperGrok OAuth must just work. Users are not OAuth experts.

What it is, in one wording used in the README, the repository description and the module docstring: use your SuperGrok or X Premium+ subscription in Home Assistant for Assist chat, voice and images, with no API key. Claims stay modest: no "only", no "first", no "best".

## Style

- Plain, short, direct. No fluff and no marketing words such as seamless, powerful, robust, effortless, unlock, supercharge or leverage. No "Let's", no em dashes, no parentheticals in UI text or the README.
- A hint is one short sentence, and most fields need none. Delete a hint that restates its label. No protocol jargon in setup text: PKCE, CSRF, loopback and redirect URI stay in the code.
- Use Home Assistant's names: Assist, AI Task, conversation agent, Voice assistants, actions. Write "SI" instead of "AI" only in text that is entirely ours; "AI Task" and "xAI" are names and stay.
- Generic examples. No house-specific names, hostnames or third-party apps in code, docs or examples. Use `example.com`.
- UI paths are bold and joined with ">": **Settings** > **Devices & services**.
- `strings.json` and `translations/en.json` stay identical; `tests/test_translations.py` checks it.

## Code

- Small commits with a message body. One concern per commit.
- `ruff check custom_components tests` and `pytest -q` pass for every commit.
- Logs never contain tokens, email addresses or account names.
- Comments say why, not what. A docstring does not restate the function name.

## Docs and releases

- The README is for user-visible things: install, features, setup, actions, troubleshooting. Internals go in the code or TESTING.md.
- CHANGELOG lines are user-visible changes, one line each, no final period, no function or class names.
- Every user-facing change updates the README and CHANGELOG in the same release. Docs never name specific Grok models; say Grok Latest or Imagine Latest.
- The version is `version` in `manifest.json`. Pushing the tag creates the GitHub Release from that CHANGELOG section and attaches `supergrok.zip`. TESTING.md is the release checklist.

## Rules and what enforces them

A rule that nothing enforces is a wish. Before you push, run `.agent-checks/bin/agent-checks local` (the shared `agent-checks` repo on Helvio's own git server) and `pytest -q -rs`.

| Rule | Enforced by |
|---|---|
| Every commit is Helvio Pedreschi <helvio88@gmail.com>, nobody else | git refuses to guess an identity on the box; `identity` check on every push |
| No em dashes, no marketing words | `voice` check on added lines, whole tree weekly |
| README and CHANGELOG move with every user-facing change; otherwise `Docs: skip <reason>` in a commit message | `docs` check |
| No built-in model ids; no house names or third-party apps in shipped code | `model-ids` and `private` checks; legacy matches carry a dated `model-id-ok` exception |
| Tests never type the release version | `tests/test_repo_rules.py` |
| Tag and `manifest.json` version agree before anything is published | `gate` job in `release.yml` |
| The release page has `supergrok.zip` | `verify` job in `release.yml` |
| Workflow files are valid | `actionlint` inside the `agent-checks` job |
| A skipped test is a failure | `no-skips` around `pytest` in `test.yml` |
| A failing nightly is seen | `alert` job opens an issue |
| Work on a branch. Never push to the default branch, never open a pull request unless asked | branch protection on the default branch; Helvio or Rook merges |
| Short, human tone in public drafts; what to build next | judgment, not enforceable |
