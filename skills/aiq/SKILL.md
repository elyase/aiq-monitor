---
name: aiq
description: "AI Quota — monitor 5h/7d usage limits and switch accounts for Claude Max, GPT Pro (Codex), and Gemini Ultra subscriptions. Use when the user hits rate limits, asks about remaining quota, wants to switch accounts, or before long-running tasks to check headroom."
---

# aiq — AI Quota

Monitor usage quotas and manage AI coding subscription accounts from the terminal.

## Setup

```bash
# Install (requires Python 3.12+ and uv)
uv tool install aiq-monitor
```

If `aiq` is not on PATH, install from source:
```bash
git clone https://github.com/elyase/aiq.git /tmp/aiq && uv tool install /tmp/aiq
```

## First run

```bash
# Import existing credentials (detects active logins for Claude, Codex, Gemini)
aiq import

# Or manually add the current session
aiq add claude alice@example.com
aiq add codex alice@example.com
```

## Commands

```bash
aiq                        # show quota table (5h/7d usage, all accounts)
aiq --json                 # machine-readable output

aiq use codex              # auto-pick best account (most 7d headroom)
aiq use codex alice@x      # switch to specific account

aiq add codex alice@x      # save current auth as vault profile
aiq logout codex           # remove auth files (log out)
aiq ls                     # list all vault profiles
aiq rm codex alice@x       # remove a vault profile
aiq import                 # import from active credentials or caam vault
```

## When to use

- User hits a rate limit → run `aiq` to check all accounts, then `aiq use <tool>` to switch
- User asks about remaining quota → run `aiq --json` and report the numbers
- Before a long agent task → check `aiq --json` to confirm sufficient headroom
- User wants to add a new account → guide through `aiq logout <tool>` → login → `aiq add <tool> <email>`

## Providers

| Tool | Quota API | Switching |
|------|-----------|-----------|
| Claude (Max) | 5h + 7d windows via `api.anthropic.com` | Swaps `~/.claude.json` + Keychain |
| Codex (GPT Pro) | 5h + 7d windows via `chatgpt.com` | Swaps `~/.codex/auth.json` |
| Gemini (Ultra) | No known API — discovery only | Swaps `~/.gemini/settings.json` |
