"""aiq — AI Quota monitor and account manager.

Check usage across Claude Max, GPT Pro, and Gemini Ultra accounts.
Manage profiles and switch accounts when you hit rate limits.

Usage:
    aiq                              # show quota for all accounts
    aiq use codex                    # auto-pick best Codex account
    aiq use claude alice@x           # switch to specific account
    aiq add codex alice@x            # save current auth as profile
    aiq logout codex                 # remove auth files
    aiq ls                           # list all vault profiles
    aiq rm codex alice@x             # remove a vault profile
    aiq import                       # import from caam vault / active creds
    aiq --json                       # machine-readable output
"""
from __future__ import annotations

from humancli import App, Context

app = App(
    "aiq",
    description="AI Quota — monitor and switch AI coding accounts.",
    version="0.1.0",
)

from aiq_cli.commands import use_app, add_app, logout_app, rm_app, ls_app, import_app  # noqa: E402
app.mount(use_app, name="use")
app.mount(add_app, name="add")
app.mount(logout_app, name="logout")
app.mount(rm_app, name="rm")
app.mount(ls_app, name="ls")
app.mount(import_app, name="import")


@app.default
def status(ctx: Context):
    """Show quota usage for all accounts across all AI tools."""
    from aiq_cli.providers import discover_all
    from aiq_cli.display import format_table

    accounts = discover_all()

    if ctx.format in ("human", "toon"):
        return format_table(accounts)
    return [a.to_dict() for a in accounts]


def main():
    app()
