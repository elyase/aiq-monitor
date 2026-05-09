"""Account discovery and usage fetching for AI coding subscriptions."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Vault & auth paths
# ---------------------------------------------------------------------------

VAULT_ROOT = Path.home() / ".local" / "share" / "aiq" / "vault"
CAAM_VAULT = Path.home() / ".local" / "share" / "caam" / "vault"

CLAUDE_AUTH_FILES: dict[str, Path] = {
    ".claude.json": Path.home() / ".claude.json",
    "settings.json": Path.home() / ".claude" / "settings.json",
}
GEMINI_AUTH_FILES: dict[str, Path] = {
    "settings.json": Path.home() / ".gemini" / "settings.json",
    "oauth_credentials.json": Path.home() / ".gemini" / "oauth_credentials.json",
    ".env": Path.home() / ".gemini" / ".env",
}

_CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_BETA = "oauth-2025-04-20"
_CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_HTTP_TIMEOUT = 10


def codex_home() -> Path:
    """Return the active Codex home, honoring CODEX_HOME when set."""
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


CODEX_AUTH = codex_home() / "auth.json"


def vault_root() -> Path:
    """Return the active vault root — prefer aiq, fall back to caam."""
    if VAULT_ROOT.is_dir():
        return VAULT_ROOT
    if CAAM_VAULT.is_dir():
        return CAAM_VAULT
    return VAULT_ROOT


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Account:
    tool: str
    email: str
    is_active: bool = False
    status: str = "unknown"  # ok | limited | expired | error | unknown
    five_hour_pct: float | None = None
    seven_day_pct: float | None = None
    five_hour_reset: float = 0.0
    seven_day_reset: float = 0.0
    error: str | None = None
    plan_type: str = ""
    model_quotas: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "tool": self.tool,
            "email": self.email,
            "active": self.is_active,
            "status": self.status,
        }
        if self.plan_type:
            d["plan"] = self.plan_type
        if self.five_hour_pct is not None:
            d["5h_used"] = round(self.five_hour_pct, 1)
            d["5h_reset"] = format_duration(self.five_hour_reset)
        if self.seven_day_pct is not None:
            d["7d_used"] = round(self.seven_day_pct, 1)
            d["7d_reset"] = format_duration(self.seven_day_reset)
        if self.model_quotas:
            d["models"] = self.model_quotas
        if self.error:
            d["error"] = self.error
        return d


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds <= 0:
        return "now"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    if d > 0:
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m"
    return f"{int(seconds)}s"


def _decode_jwt(token: str) -> dict[str, object]:
    """Decode JWT payload without signature verification.

    Only used to extract display metadata (email, plan type) from local tokens.
    Not used for authentication or trust decisions.
    """
    if not token or token.count(".") < 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (json.JSONDecodeError, ValueError):
        return {}


def _http_get(url: str, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    """GET request returning (status_code, parsed_json). Returns (0, {}) on network error."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body: dict[str, object] = {}
        try:
            body = json.loads(e.read().decode("utf-8"))
        except (json.JSONDecodeError, ValueError):
            pass
        return e.code, body
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _first(*values: object) -> object:
    """Return the first non-None value, defaulting to 0."""
    for v in values:
        if v is not None:
            return v
    return 0


def _first_str(mapping: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _parse_iso_reset(s: str) -> float:
    """Parse ISO timestamp, return seconds until reset."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except ValueError:
        return 0.0


def copy_secure(src: Path, dst: Path) -> None:
    """Copy a file with 0o600 permissions."""
    import shutil
    old_umask = os.umask(0o177)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    finally:
        os.umask(old_umask)
    os.chmod(str(dst), 0o600)


# ---------------------------------------------------------------------------
# Claude provider
# ---------------------------------------------------------------------------

def claude_active_email() -> str | None:
    """Get the email of the currently active Claude account."""
    data = _read_json(CLAUDE_AUTH_FILES[".claude.json"])
    if not data:
        return None
    oauth = data.get("oauthAccount")
    return oauth.get("emailAddress") if isinstance(oauth, dict) else None


def _read_keychain_claude() -> dict[str, object] | None:
    """Read Claude OAuth token from macOS Keychain."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.strip())
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _claude_fetch_usage(token: str) -> Account:
    status_code, data = _http_get(_CLAUDE_USAGE_URL, {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": _CLAUDE_BETA,
        "User-Agent": "aiq/0.1",
    })

    if status_code == 401:
        return Account(tool="claude", email="", status="expired", error="Token expired — run: claude /login")
    if status_code == 403:
        return Account(tool="claude", email="", status="error", error="Access denied (403)")
    if status_code == 429:
        return Account(tool="claude", email="", status="limited", error="Usage API throttled (429)")
    if status_code == 0:
        return Account(tool="claude", email="", status="error", error="Network error")
    if status_code != 200:
        return Account(tool="claude", email="", status="error", error=f"HTTP {status_code}")

    fh = data.get("five_hour") or data.get("fiveHour") or {}
    fh_pct = _first(fh.get("utilization"), fh.get("usage_pct"))
    fh_reset_str = fh.get("resets_at") or fh.get("reset_at") or fh.get("resetAt") or ""
    fh_reset = _parse_iso_reset(fh_reset_str) if fh_reset_str else 0.0

    sd = data.get("seven_day") or data.get("sevenDay") or {}
    sd_pct = _first(sd.get("utilization"), sd.get("usage_pct"))
    sd_reset_str = sd.get("resets_at") or sd.get("reset_at") or sd.get("resetAt") or ""
    sd_reset = _parse_iso_reset(sd_reset_str) if sd_reset_str else 0.0

    models_raw = data.get("models") or data.get("model_quotas") or []
    model_quotas = [
        {"model": m.get("model") or m.get("name") or "?",
         "used_pct": _first(m.get("utilization"), m.get("usage_pct"))}
        for m in models_raw
    ]
    plan = data.get("plan_type") or data.get("planType") or ""
    limited = fh_pct >= 100 or sd_pct >= 100

    return Account(
        tool="claude", email="", is_active=True,
        status="limited" if limited else "ok",
        five_hour_pct=fh_pct, seven_day_pct=sd_pct,
        five_hour_reset=fh_reset, seven_day_reset=sd_reset,
        plan_type=plan, model_quotas=model_quotas,
    )


def _discover_claude() -> list[Account]:
    vault_dir = vault_root() / "claude"
    active = claude_active_email()
    accounts: list[Account] = []
    emails = sorted(p.name for p in vault_dir.iterdir()) if vault_dir.is_dir() else []

    for email in emails:
        if email == active:
            keychain = _read_keychain_claude()
            oauth = keychain.get("claudeAiOauth", {}) if keychain else {}
            token = oauth.get("accessToken") if isinstance(oauth, dict) else None
            if token:
                acct = _claude_fetch_usage(token)
                acct.email = email
                acct.is_active = True
                accounts.append(acct)
            else:
                accounts.append(Account(tool="claude", email=email, is_active=True, status="error", error="No Keychain token"))
        else:
            accounts.append(Account(tool="claude", email=email, status="unknown"))

    if active and active not in emails:
        keychain = _read_keychain_claude()
        oauth = keychain.get("claudeAiOauth", {}) if keychain else {}
        token = oauth.get("accessToken") if isinstance(oauth, dict) else None
        if token:
            acct = _claude_fetch_usage(token)
            acct.email = active
            acct.is_active = True
            accounts.insert(0, acct)

    return accounts


# ---------------------------------------------------------------------------
# Codex provider
# ---------------------------------------------------------------------------

def _codex_email_from_jwt(token: str) -> str:
    claims = _decode_jwt(token)
    profile = claims.get("https://api.openai.com/profile", {})
    return profile.get("email", "unknown") if isinstance(profile, dict) else "unknown"


def _codex_plan_from_jwt(token: str) -> str:
    claims = _decode_jwt(token)
    auth = claims.get("https://api.openai.com/auth", {})
    return auth.get("chatgpt_plan_type", "") if isinstance(auth, dict) else ""


def _codex_tokens(auth: dict[str, object]) -> dict[str, object] | None:
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        return None
    return tokens


def _codex_access_token(auth: dict[str, object]) -> str | None:
    api_key = auth.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip()

    tokens = _codex_tokens(auth)
    if not tokens:
        return None
    return _first_str(tokens, "access_token", "accessToken")


def _codex_id_token(auth: dict[str, object]) -> str | None:
    tokens = _codex_tokens(auth)
    if not tokens:
        return None
    return _first_str(tokens, "id_token", "idToken")


def _codex_account_id(auth: dict[str, object]) -> str | None:
    tokens = _codex_tokens(auth)
    if not tokens:
        return None
    return _first_str(tokens, "account_id", "accountId")


def _codex_email_from_auth(auth: dict[str, object]) -> str | None:
    access = _codex_access_token(auth)
    id_token = _codex_id_token(auth)
    for token in (id_token, access):
        if not token:
            continue
        email = _codex_email_from_jwt(token)
        if email != "unknown":
            return email
    return None


def _codex_plan_from_auth(auth: dict[str, object]) -> str:
    access = _codex_access_token(auth)
    id_token = _codex_id_token(auth)
    for token in (id_token, access):
        if not token:
            continue
        plan = _codex_plan_from_jwt(token)
        if plan:
            return plan
    return ""


def _codex_usage_url() -> str:
    config = _read_text(codex_home() / "config.toml")
    base_url = ""
    if config:
        for raw_line in config.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != "chatgpt_base_url":
                continue
            base_url = value.strip().strip("\"'")
            break

    if not base_url:
        return _CODEX_USAGE_URL

    base_url = base_url.rstrip("/")
    if base_url.startswith(("https://chatgpt.com", "https://chat.openai.com")) and "/backend-api" not in base_url:
        base_url += "/backend-api"
    path = "/wham/usage" if "/backend-api" in base_url else "/api/codex/usage"
    return base_url + path


def _codex_reset_seconds(window: dict[str, object]) -> float:
    reset_after = window.get("reset_after_seconds")
    if isinstance(reset_after, (int, float)):
        return float(reset_after)
    if isinstance(reset_after, str):
        try:
            return float(reset_after)
        except ValueError:
            pass

    reset_at = window.get("reset_at")
    if isinstance(reset_at, (int, float)):
        return max(0.0, float(reset_at) - time.time())
    if isinstance(reset_at, str):
        try:
            return max(0.0, float(reset_at) - time.time())
        except ValueError:
            return 0.0
    return 0.0


def codex_active_identity() -> tuple[str | None, str | None, str | None]:
    """Return (email, access_token, account_id) for active Codex account."""
    data = _read_json(CODEX_AUTH)
    if not data:
        return None, None, None
    access = _codex_access_token(data)
    account_id = _codex_account_id(data)
    email = _codex_email_from_auth(data)
    return email, access, account_id


def _codex_fetch_usage(access_token: str, account_id: str | None) -> Account:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "aiq/0.1",
        "Accept": "application/json",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id

    status_code, data = _http_get(_codex_usage_url(), headers)

    if status_code == 401:
        return Account(tool="codex", email="", status="expired", error="Token expired — run: codex login")
    if status_code == 403:
        return Account(tool="codex", email="", status="error", error="Access denied (403)")
    if status_code == 429:
        return Account(tool="codex", email="", status="limited", error="API throttled (429)")
    if status_code == 0:
        return Account(tool="codex", email="", status="error", error="Network error")
    if status_code != 200:
        return Account(tool="codex", email="", status="error", error=f"HTTP {status_code}")

    rate_limit = data.get("rate_limit") or {}
    primary = rate_limit.get("primary_window") or {} if isinstance(rate_limit, dict) else {}
    secondary = rate_limit.get("secondary_window") or {} if isinstance(rate_limit, dict) else {}

    fh_pct = primary.get("used_percent", 0)
    fh_reset = _codex_reset_seconds(primary)
    sd_pct = secondary.get("used_percent", 0)
    sd_reset = _codex_reset_seconds(secondary)

    limited = isinstance(rate_limit, dict) and (
        rate_limit.get("limit_reached", False) or not rate_limit.get("allowed", True))

    return Account(
        tool="codex", email="",
        status="limited" if limited else "ok",
        five_hour_pct=fh_pct, seven_day_pct=sd_pct,
        five_hour_reset=fh_reset, seven_day_reset=sd_reset,
        plan_type=data.get("plan_type", ""),
    )


def _discover_codex() -> list[Account]:
    vault_dir = vault_root() / "codex"
    active_email, active_access, active_account_id = codex_active_identity()
    accounts: list[Account] = []
    emails = sorted(p.name for p in vault_dir.iterdir()) if vault_dir.is_dir() else []

    def fetch_one(email: str) -> Account:
        vault_auth = _read_json(vault_dir / email / "auth.json")
        if not vault_auth:
            return Account(tool="codex", email=email, status="error", error="No auth.json in vault")
        access = _codex_access_token(vault_auth)
        account_id = _codex_account_id(vault_auth)
        if not access:
            return Account(tool="codex", email=email, status="error", error="Missing access token")

        exp = _decode_jwt(access).get("exp")
        if isinstance(exp, (int, float)) and exp < time.time():
            return Account(tool="codex", email=email, is_active=(email == active_email),
                           status="expired", error="Token expired", plan_type=_codex_plan_from_auth(vault_auth))

        acct = _codex_fetch_usage(access, account_id)
        acct.email = email
        acct.is_active = (email == active_email)
        if not acct.plan_type:
            acct.plan_type = _codex_plan_from_auth(vault_auth)
        return acct

    if emails:
        with ThreadPoolExecutor(max_workers=min(len(emails), 10)) as pool:
            futures = {pool.submit(fetch_one, e): e for e in emails}
            for future in as_completed(futures):
                try:
                    accounts.append(future.result())
                except Exception as e:  # noqa: BLE001
                    accounts.append(Account(tool="codex", email=futures[future], status="error", error=str(e)))

    if active_email and active_email not in emails and active_access:
        active_auth = _read_json(CODEX_AUTH) or {}
        acct = _codex_fetch_usage(active_access, active_account_id)
        acct.email = active_email
        acct.is_active = True
        if not acct.plan_type:
            acct.plan_type = _codex_plan_from_auth(active_auth)
        accounts.append(acct)

    accounts.sort(key=lambda a: (not a.is_active, a.email))
    return accounts


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

def gemini_active_email() -> str | None:
    """Identify active Gemini profile by comparing settings file hash."""
    import hashlib
    settings = GEMINI_AUTH_FILES["settings.json"]
    if not settings.exists():
        return None
    active_hash = hashlib.sha256(settings.read_bytes()).hexdigest()
    vault_dir = vault_root() / "gemini"
    if not vault_dir.is_dir():
        return None
    for profile_dir in vault_dir.iterdir():
        vault_file = profile_dir / "settings.json"
        if vault_file.exists() and hashlib.sha256(vault_file.read_bytes()).hexdigest() == active_hash:
            return profile_dir.name
    return None


def _discover_gemini() -> list[Account]:
    vault_dir = vault_root() / "gemini"
    active = gemini_active_email()
    emails = sorted(p.name for p in vault_dir.iterdir()) if vault_dir.is_dir() else []
    return [Account(tool="gemini", email=e, is_active=(e == active), status="unknown") for e in emails]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_PROVIDERS = {"claude": _discover_claude, "codex": _discover_codex, "gemini": _discover_gemini}


def discover_all() -> list[Account]:
    """Discover and fetch usage for all accounts across all tools."""
    accounts: list[Account] = []
    for fn in _PROVIDERS.values():
        accounts.extend(fn())
    return accounts


def discover_tool(tool: str) -> list[Account]:
    """Discover and fetch usage for a specific tool."""
    fn = _PROVIDERS.get(tool)
    return fn() if fn else []
