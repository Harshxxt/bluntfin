"""Render findings as plain text or JSON."""
from __future__ import annotations

import json
import sys

from .analysis import Finding, Severity, priorities

TITLES = {
    "cashflow": "CASH FLOW",
    "emergency": "EMERGENCY FUND",
    "debt": "DEBT",
    "networth": "NET WORTH",
    "retirement": "RETIREMENT",
    "goals": "GOALS",
    "tax": "TAX",
}
COLORS = {
    Severity.CRITICAL: "\033[1;97;41m",
    Severity.BAD: "\033[1;31m",
    Severity.WEAK: "\033[33m",
    Severity.OK: "\033[36m",
    Severity.GOOD: "\033[32m",
}
RESET = "\033[0m"

DISCLAIMER = ("Rules of thumb, not licensed financial advice. The math is exact; the inputs are yours. "
              "If you lied to the model, it lied back.")


def _tag(sev: Severity, color: bool) -> str:
    label = f"[{sev.name}]".ljust(10)
    return f"{COLORS[sev]}{label}{RESET}" if color else label


def render_text(findings: list[Finding], color: bool | None = None) -> str:
    if color is None:
        color = sys.stdout.isatty()
    lines: list[str] = []
    section = None
    for f in findings:
        if f.section != section:
            section = f.section
            lines += ["", f"== {TITLES.get(section, section.upper())} " + "=" * 40]
        lines.append(f"{_tag(f.severity, color)} {f.headline}")
        if f.detail:
            lines.append(" " * 11 + f.detail)

    todo = priorities(findings)
    lines += ["", "== FIX THIS, IN THIS ORDER " + "=" * 26]
    if todo:
        for i, f in enumerate(todo, 1):
            lines.append(f"{i}. {f.headline}")
    else:
        lines.append("Nothing critical. Do not get comfortable; rerun this every quarter.")
    lines += ["", DISCLAIMER]
    return "\n".join(lines).lstrip("\n")


def render_json(findings: list[Finding]) -> str:
    def enc(f: Finding) -> dict:
        return {"section": f.section, "severity": f.severity.name, "headline": f.headline,
                "detail": f.detail, "metrics": f.metrics}
    return json.dumps({"findings": [enc(f) for f in findings],
                       "priorities": [f.headline for f in priorities(findings)]}, indent=2)
