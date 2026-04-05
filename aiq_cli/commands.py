"""Vault management commands: use, add, logout, rm, ls, import."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from humancli import App, Param

from aiq_cli.providers import (
    VAULT_ROOT, CAAM_VAULT,
    CLAUDE_AUTH_FILES, CODEX_AUTH, GEMINI_AUTH_FILES,
    vault_root, discover_tool,
    claude_active_email, codex_active_identity, gemini_active_email,
    copy_secure,
)

Tool = Literal["claude", "codex", "gemini"]
TOOLS: list[str] = ["claude", "codex", "gemini"]

# Files that are auth state (deleted on logout) vs. user preferences (kept)
_CLAUDE_LOGOUT_FILES = {".claude.json": CLAUDE_AUTH_FILES[".claude.json"]}
_CODEX_LOGOUT_FILES = {"auth.json": CODEX_AUTH}
_GEMINI_LOGOUT_FILES = {
    "oauth_credentials.json": GEMINI_AUTH_FILES["oauth_credentials.json"],
    ".env": GEMINI_AUTH_FILES[".env"],
}


def _auth_files(tool: str) -> dict[str, Path]:
    """Return {vault_filename: active_path} for a tool (all files, for backup/switch)."""
    match tool:
        case "claude":
            return CLAUDE_AUTH_FILES
        case "codex":
            return {"auth.json": CODEX_AUTH}
        case "gemini":
            return GEMINI_AUTH_FILES
        case _:
            return {}


def _logout_files(tool: str) -> dict[str, Path]:
    """Return {vault_filename: active_path} for auth-only files (deleted on logout)."""
    match tool:
        case "claude":
            return _CLAUDE_LOGOUT_FILES
        case "codex":
            return _CODEX_LOGOUT_FILES
        case "gemini":
            return _GEMINI_LOGOUT_FILES
        case _:
            return {}


def _active_email(tool: str) -> str | None:
    match tool:
        case "claude":
            return claude_active_email()
        case "codex":
            email, _, _ = codex_active_identity()
            return email
        case "gemini":
            return gemini_active_email()
    return None


def _vault_profiles(tool: str) -> list[str]:
    d = vault_root() / tool
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def _validate_vault_path(tool: str, email: str) -> Path | None:
    """Resolve vault path and verify it's under the vault root. Returns None if traversal detected."""
    vault_dir = (vault_root() / tool / email).resolve()
    root = vault_root().resolve()
    if not vault_dir.is_relative_to(root):
        return None
    return vault_dir


def _copy_to_vault(tool: str, email: str) -> tuple[Path, list[str]]:
    """Copy active auth files into vault. Returns (vault_dir, copied_files)."""
    vault_dir = _validate_vault_path(tool, email)
    if not vault_dir:
        return Path(), []
    vault_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    try:
        for vault_name, active_path in _auth_files(tool).items():
            if active_path.exists():
                copy_secure(active_path, vault_dir / vault_name)
                copied.append(vault_name)
        if copied:
            _write_meta(vault_dir, tool, email, copied)
        else:
            vault_dir.rmdir()
    except OSError:
        if vault_dir.is_dir():
            shutil.rmtree(vault_dir, ignore_errors=True)
        copied = []
    return vault_dir, copied


def _copy_from_vault(tool: str, email: str) -> list[str]:
    """Copy auth files from vault to active locations. Returns copied files."""
    vault_dir = _validate_vault_path(tool, email)
    if not vault_dir:
        return []
    copied = []
    for vault_name, active_path in _auth_files(tool).items():
        src = vault_dir / vault_name
        if src.exists():
            copy_secure(src, active_path)
            copied.append(vault_name)
    return copied


def _write_meta(vault_dir: Path, tool: str, email: str, files: list[str]) -> None:
    meta = {
        "tool": tool,
        "profile": email,
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "files": len(files),
    }
    (vault_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.chmod(str(vault_dir / "meta.json"), 0o600)


def _clear_keychain_claude() -> bool:
    """Remove Claude Code credentials from macOS Keychain."""
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", "Claude Code-credentials"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------

use_app = App("use", description="Switch to an account.")


@use_app.default
def use(
    tool: Annotated[Tool, Param(help="Tool to switch")],
    email: Annotated[str, Param(help="Email (omit to auto-pick best)")] = "",
):
    """Switch account. Omit email to auto-select the one with most headroom."""
    if email:
        return _activate(tool, email)
    return _auto_pick(tool)


def _activate(tool: str, email: str) -> dict:
    vault_dir = _validate_vault_path(tool, email)
    if not vault_dir:
        return {"error": "Invalid profile name"}
    if not vault_dir.is_dir():
        return {"error": f"Profile '{email}' not found", "available": _vault_profiles(tool)}
    copied = _copy_from_vault(tool, email)
    if not copied:
        return {"error": f"No auth files in vault for {email}"}
    return {"action": "switched", "tool": tool, "email": email, "files": copied}


def _auto_pick(tool: str) -> dict:
    accounts = discover_tool(tool)
    if not accounts:
        return {"error": f"No {tool} accounts in vault"}

    available = [a for a in accounts if a.status == "ok" and a.seven_day_pct is not None]
    if available:
        best = min(available, key=lambda a: (a.seven_day_pct or 0, a.five_hour_pct or 0))
    else:
        non_active = [a for a in accounts if not a.is_active]
        if not non_active:
            return {"error": "All accounts rate-limited, no alternatives"}
        non_active.sort(key=lambda a: (a.status == "limited", a.five_hour_reset))
        best = non_active[0]

    if best.is_active:
        return {"action": "none", "reason": "already_best", "tool": tool, "email": best.email, "7d_used": best.seven_day_pct}

    result = _activate(tool, best.email)
    result["auto"] = True
    result["reason"] = f"7d: {best.seven_day_pct:.0f}% used" if best.seven_day_pct is not None else "round-robin"
    return result


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

add_app = App("add", description="Save current auth as a vault profile.")


@add_app.default
def add(
    tool: Annotated[Tool, Param(help="Tool to save")],
    email: Annotated[str, Param(help="Email label for this profile")],
):
    """Save current auth files to the vault."""
    if not _validate_vault_path(tool, email):
        return {"error": "Invalid profile name"}
    vault_dir, copied = _copy_to_vault(tool, email)
    if not copied:
        return {"error": f"No auth files found for {tool}"}
    return {"action": "added", "tool": tool, "email": email, "files": copied, "vault": str(vault_dir)}


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------

logout_app = App("logout", description="Remove auth files for a tool.")


@logout_app.default
def logout(
    tool: Annotated[Tool, Param(help="Tool to log out of")],
):
    """Remove auth files for a tool (log out). Preserves user preferences like settings."""
    removed = []
    for _, active_path in _logout_files(tool).items():
        if active_path.exists():
            active_path.unlink()
            removed.append(active_path.name)

    # Claude: also clear macOS Keychain token
    if tool == "claude":
        if _clear_keychain_claude():
            removed.append("keychain")

    if not removed:
        return {"action": "none", "tool": tool, "reason": "already logged out"}
    return {"action": "logged_out", "tool": tool, "files": removed}


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------

rm_app = App("rm", description="Remove a vault profile.")


@rm_app.default
def rm(
    tool: Annotated[Tool, Param(help="Tool")],
    email: Annotated[str, Param(help="Profile email to remove")],
):
    """Remove a saved profile from the vault."""
    vault_dir = _validate_vault_path(tool, email)
    if not vault_dir:
        return {"error": "Invalid profile name"}
    if not vault_dir.is_dir():
        return {"error": f"Profile '{email}' not found", "available": _vault_profiles(tool)}
    shutil.rmtree(vault_dir)
    return {"action": "removed", "tool": tool, "email": email}


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------

ls_app = App("ls", description="List vault profiles.")


@ls_app.default
def ls(
    tool: Annotated[str, Param(help="Tool (or 'all')")] = "all",
):
    """List all saved profiles in the vault."""
    tools = [tool] if tool != "all" else TOOLS
    profiles: list[dict] = []
    for t in tools:
        active = _active_email(t)
        for email in _vault_profiles(t):
            profiles.append({"tool": t, "email": email, "active": email == active})
    return profiles


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

import_app = App("import", description="Import from caam vault and active credentials.")


@import_app.default
def import_profiles():
    """Discover and import profiles from caam vault and active credentials."""
    results: list[dict] = []
    vault = vault_root()

    # 1. Copy from CAAM vault if it exists separately (allowlisted files only)
    _ALLOWED_FILES = {
        "claude": {".claude.json", "settings.json", "meta.json"},
        "codex": {"auth.json", "meta.json"},
        "gemini": {"settings.json", "oauth_credentials.json", ".env", "meta.json"},
    }
    if CAAM_VAULT.is_dir() and vault != CAAM_VAULT:
        VAULT_ROOT.mkdir(parents=True, exist_ok=True)
        for tool_dir in sorted(CAAM_VAULT.iterdir()):
            if not tool_dir.is_dir():
                continue
            tool = tool_dir.name
            allowed = _ALLOWED_FILES.get(tool, set())
            for profile_dir in sorted(tool_dir.iterdir()):
                if not profile_dir.is_dir() or profile_dir.name.startswith("_"):
                    continue
                email = profile_dir.name
                dest = VAULT_ROOT / tool / email
                if dest.is_dir():
                    results.append({"source": "caam", "tool": tool, "email": email, "action": "skipped"})
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                copied = False
                for f in profile_dir.iterdir():
                    if f.is_file() and f.name in allowed:
                        copy_secure(f, dest / f.name)
                        copied = True
                if copied:
                    results.append({"source": "caam", "tool": tool, "email": email, "action": "imported"})
                else:
                    dest.rmdir()

    # 2. Detect active credentials not yet in vault
    for tool, detect_fn in [
        ("claude", claude_active_email),
        ("codex", lambda: codex_active_identity()[0]),
        ("gemini", gemini_active_email),
    ]:
        email = detect_fn()
        if not email or email == "unknown":
            continue
        if (vault_root() / tool / email).is_dir():
            results.append({"source": "active", "tool": tool, "email": email, "action": "skipped"})
            continue
        _, copied = _copy_to_vault(tool, email)
        if copied:
            results.append({"source": "active", "tool": tool, "email": email, "action": "imported", "files": copied})

    if not results:
        return {"message": "Nothing found. Log in to a tool first, then run aiq import."}

    imported = sum(1 for r in results if r["action"] == "imported")
    return {"imported": imported, "skipped": len(results) - imported, "details": results}
