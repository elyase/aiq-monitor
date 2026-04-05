# aiq — AI Quota

Monitor usage and switch between AI coding subscription accounts.

```
$ aiq
  AI Quota
  ────────────────────────────────────────────────────────────────────────
    TOOL     ACCOUNT                          5h            7d          STATUS
  ● claude   alice@example.com       ██████░░░░  62.3%  ████░░░░░░  38.1%  OK
    claude   bob@example.com                  —              —        --
  ────────────────────────────────────────────────────────────────────────
  ● codex    alice@example.com       ████████░░  81.0%  ██████████ 100.0%  LIMITED
    codex    bob@example.com         ██░░░░░░░░  15.2%  ███░░░░░░░  28.4%  OK
  ────────────────────────────────────────────────────────────────────────
  4 accounts · 2 ok · 1 limited · 1 unknown
```

## What it does

- **Check quotas** across Claude Max, Codex (ChatGPT Pro), and Gemini Ultra accounts
- **Switch accounts** when you hit rate limits — auto-picks the one with most headroom
- **Vault management** — save, list, and remove credential profiles locally

## Install

```bash
uv tool install aiq
```

Or from source:

```bash
git clone https://github.com/elyase/aiq.git
cd aiq
uv tool install .
```

## Usage

```bash
aiq                        # show quota for all accounts
aiq use codex              # auto-pick best Codex account
aiq use claude alice@x     # switch to specific account
aiq add codex alice@x      # save current auth as profile
aiq logout codex           # remove auth files
aiq ls                     # list all vault profiles
aiq rm codex alice@x       # remove a vault profile
aiq import                 # import from caam vault / active creds
aiq --json                 # machine-readable output
```

## How credentials work

aiq never stores passwords or API keys. It copies the OAuth/session files that each tool's CLI already writes to disk (e.g., `~/.codex/auth.json`) into a local vault at `~/.local/share/aiq/vault/`. All vault files are stored with `0600` permissions.

Supported tools and their auth sources:

| Tool | Auth files read |
|------|----------------|
| Claude | `~/.claude.json`, macOS Keychain (`Claude Code-credentials`) |
| Codex | `~/.codex/auth.json` |
| Gemini | `~/.gemini/settings.json`, `~/.gemini/oauth_credentials.json` |

## Requirements

- Python 3.12+
- macOS (Keychain access for Claude; other tools work cross-platform)
- [humancli](https://github.com/elyase/agentcli) framework

## License

MIT
