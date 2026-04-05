"""ANSI terminal display for aiq."""
from __future__ import annotations

import sys

from aiq_cli.providers import Account, format_duration

_ANSI = {
    "R": "\033[0m", "B": "\033[1m", "D": "\033[2m",
    "red": "\033[31m", "grn": "\033[32m", "yel": "\033[33m",
    "cyn": "\033[36m", "bgr": "\033[41m", "wht": "\033[37m",
}


def _a() -> dict[str, str]:
    """Return ANSI codes if stdout is a tty, empty strings otherwise."""
    return _ANSI if sys.stdout.isatty() else {k: "" for k in _ANSI}


def _bar(pct: float, width: int = 10) -> str:
    a = _a()
    filled = max(0, min(width, round(pct / 100 * width)))
    empty = width - filled
    color = a["red"] if pct >= 100 else a["yel"] if pct >= 75 else a["grn"]
    return f"{color}{'█' * filled}{a['D']}{'░' * empty}{a['R']}"


def _pct_color(pct: float) -> str:
    a = _a()
    if pct >= 100:
        return f"{a['bgr']}{a['wht']}{a['B']}{pct:5.1f}%{a['R']}"
    if pct >= 75:
        return f"{a['red']}{pct:5.1f}%{a['R']}"
    if pct >= 50:
        return f"{a['yel']}{pct:5.1f}%{a['R']}"
    return f"{a['grn']}{pct:5.1f}%{a['R']}"


_STATUS_MAP = {
    "ok": ("grn", "OK"), "limited": ("red", "LIMITED"),
    "expired": ("red", "EXPIRED"), "error": ("red", "ERR"),
}


def _status_icon(status: str) -> str:
    a = _a()
    color_key, label = _STATUS_MAP.get(status, ("D", "--"))
    return f"{a[color_key]}{label}{a['R']}"


def format_table(accounts: list[Account]) -> str:
    """Format accounts into a terminal table with usage bars."""
    a = _a()
    if not accounts:
        return f"  {a['red']}No accounts found.{a['R']} Run `aiq add <tool> <email>` to save a profile."

    lines: list[str] = [""]
    lines.append(f"  {a['B']}{a['cyn']}AI Quota{a['R']}")
    lines.append(f"  {a['D']}{'─' * 72}{a['R']}")
    lines.append(f"  {a['B']}{'':2} {'TOOL':<8} {'ACCOUNT':<32} {'5h':>12}  {'7d':>12}  {'STATUS':<10}{a['R']}")

    current_tool = ""
    for acct in accounts:
        if acct.tool != current_tool:
            if current_tool:
                lines.append(f"  {a['D']}{'─' * 72}{a['R']}")
            current_tool = acct.tool

        marker = f"{a['grn']}●{a['R']}" if acct.is_active else " "
        email = acct.email[:30] + ".." if len(acct.email) > 32 else acct.email

        fh = f"{_bar(acct.five_hour_pct, 6)} {_pct_color(acct.five_hour_pct)}" if acct.five_hour_pct is not None else f"{a['D']}{'—':>12}{a['R']}"
        sd = f"{_bar(acct.seven_day_pct, 6)} {_pct_color(acct.seven_day_pct)}" if acct.seven_day_pct is not None else f"{a['D']}{'—':>12}{a['R']}"

        lines.append(f"  {marker} {acct.tool:<8} {email:<32} {fh}  {sd}  {_status_icon(acct.status)}")

        if acct.status == "limited":
            resets = []
            if acct.five_hour_pct is not None and acct.five_hour_pct >= 100:
                resets.append(f"5h resets in {format_duration(acct.five_hour_reset)}")
            if acct.seven_day_pct is not None and acct.seven_day_pct >= 100:
                resets.append(f"7d resets in {format_duration(acct.seven_day_reset)}")
            if resets:
                lines.append(f"  {'':2} {'':8} {a['D']}{', '.join(resets)}{a['R']}")

        if acct.error and acct.status in ("expired", "error"):
            lines.append(f"  {'':2} {'':8} {a['D']}{acct.error}{a['R']}")

    lines.append(f"  {a['D']}{'─' * 72}{a['R']}")

    ok = sum(1 for x in accounts if x.status == "ok")
    limited = sum(1 for x in accounts if x.status == "limited")
    errors = sum(1 for x in accounts if x.status in ("error", "expired"))
    unknown = sum(1 for x in accounts if x.status == "unknown")

    parts = [f"{a['B']}{len(accounts)} accounts{a['R']}"]
    if ok:
        parts.append(f"{a['grn']}{ok} ok{a['R']}")
    if limited:
        parts.append(f"{a['red']}{limited} limited{a['R']}")
    if errors:
        parts.append(f"{a['red']}{errors} errors{a['R']}")
    if unknown:
        parts.append(f"{a['D']}{unknown} unknown{a['R']}")
    lines.append(f"  {' · '.join(parts)}")
    lines.append("")
    return "\n".join(lines)
