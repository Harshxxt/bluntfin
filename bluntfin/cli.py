"""Command line interface.

    bluntfin init my.json                 # answer questions, writes a profile
    bluntfin analyze my.json              # full report
    bluntfin analyze my.json --only debt,retirement
    bluntfin analyze my.json --extra 500  # what if I throw $500/mo extra at debt?
    bluntfin analyze my.json --json       # machine-readable
    bluntfin serve                        # local website at http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analysis import SECTIONS, run
from .countries import apply_country
from .model import DEBT_KINDS, ProfileError, load_profile, profile_from_dict
from .report import render_json, render_text


def _ask(prompt: str, cast=float, default=None, allow_blank=True):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw:
            if default is not None or allow_blank:
                return default
            print("  Required.")
            continue
        try:
            value = cast(raw.replace(",", ""))
        except ValueError:
            print("  Not a valid number. Try again.")
            continue
        if isinstance(value, (int, float)) and value < 0:
            print("  Cannot be negative.")
            continue
        return value


def _yes(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower().startswith("y")


def interactive_profile() -> dict:
    print("Answer honestly. Monthly figures unless stated. Leave blank to skip optional fields.\n")
    d: dict = {}
    d["currency"] = input("Currency symbol [$]: ").strip() or "$"
    d["net_monthly_income"] = _ask("Take-home pay per month (after tax)", allow_blank=False)
    d["gross_annual_income"] = _ask("Gross annual income (before tax)")
    d["age"] = _ask("Age", int)
    d["retirement_age"] = _ask("Target retirement age", int, 65)
    system = input("Tax system: us / uk / india_new / none [none]: ").strip().lower()
    if system in ("us", "uk", "india_new"):
        tax = {"system": system}
        if system == "us":
            tax["filing"] = "married" if input("  Filing: single / married [single]: ").strip().lower().startswith("m") else "single"
            state = _ask("  State + local income tax rate %", default=0)
            if state:
                tax["state_tax_pct"] = state
        d["tax"] = tax

    print("\nMonthly living expenses. Do NOT include debt payments here; those come next.")
    expenses = {}
    for cat in ["housing", "utilities", "groceries", "transport", "insurance", "dining_out",
                "subscriptions", "shopping", "other"]:
        v = _ask(f"  {cat}")
        if v:
            expenses[cat] = v
    while _yes("  Add another category?"):
        name = input("    name: ").strip()
        if name:
            expenses[name] = _ask(f"    {name}", allow_blank=False)
    d["monthly_expenses"] = expenses or {"other": 0.0}

    print("\nAssets")
    d["cash"] = _ask("  Cash in bank/savings", default=0)
    d["investments"] = _ask("  Investments + retirement accounts", default=0)
    d["other_assets"] = _ask("  Other assets: full market value of home, car, etc. (loans go under debts)", default=0)
    d["payroll_savings_monthly"] = _ask("  Retirement contributions taken from paycheck before take-home",
                                        default=0)
    d["monthly_investing"] = _ask("  Amount you invest from take-home each month", default=0)

    print(f"\nDebts. Kinds: {', '.join(sorted(DEBT_KINDS))}")
    debts = []
    while _yes("Add a debt?"):
        kind = input("  kind [other]: ").strip() or "other"
        debts.append({
            "name": input("  name: ").strip() or kind,
            "kind": kind if kind in DEBT_KINDS else "other",
            "balance": _ask("  balance", allow_blank=False),
            "apr": _ask("  APR %", allow_blank=False),
            "min_payment": _ask("  minimum monthly payment", allow_blank=False),
        })
    d["debts"] = debts

    print("\nGoals (house deposit, car, wedding...)")
    goals = []
    while _yes("Add a goal?"):
        goals.append({
            "name": input("  name: ").strip() or "goal",
            "target": _ask("  target amount", allow_blank=False),
            "months": _ask("  months until deadline", int, allow_blank=False),
            "saved": _ask("  already saved", default=0),
        })
    d["goals"] = goals
    return {k: v for k, v in d.items() if v is not None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bluntfin", description="A financial model that does not flatter you.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="build a profile by answering questions")
    p_init.add_argument("output", help="where to write the profile JSON")
    p_init.add_argument("--analyze", action="store_true", help="run the analysis right after")

    p_srv = sub.add_parser("serve", help="run the local website")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)

    p_an = sub.add_parser("analyze", help="analyze a profile")
    p_an.add_argument("profile", help="profile JSON file ('-' for stdin)")
    p_an.add_argument("--only", help=f"comma-separated sections: {','.join(SECTIONS)}")
    p_an.add_argument("--extra", type=float, help="extra monthly amount for debt payoff (default: your free surplus)")
    p_an.add_argument("--json", action="store_true", help="output JSON")
    p_an.add_argument("--runs", type=int, default=5000, help="Monte Carlo simulations (default 5000)")
    p_an.add_argument("--seed", type=int, help="random seed for reproducible Monte Carlo results")
    p_an.add_argument("--no-monte-carlo", action="store_true", help="straight-line projection only")
    p_an.add_argument("--no-color", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve
        serve(args.host, args.port)
        return 0

    try:
        if args.cmd == "init":
            data = interactive_profile()
            profile_from_dict(data)  # validate before writing
            Path(args.output).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print(f"\nWrote {args.output}.")
            if not args.analyze:
                return 0
            profile = load_profile(args.output)
            sections, extra, as_json, color = None, None, False, None
            mc, runs, seed = True, 5000, None
        else:
            if args.profile == "-":
                raw = json.load(sys.stdin)
            elif not Path(args.profile).is_file():
                raise ProfileError(f"Profile file not found: {args.profile}")
            else:
                raw = json.loads(Path(args.profile).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ProfileError("Profile must be a JSON object.")
            profile = profile_from_dict(apply_country(raw))
            sections = [s.strip() for s in args.only.split(",")] if args.only else None
            extra, as_json = args.extra, args.json
            color = False if args.no_color else None
            mc, runs, seed = not args.no_monte_carlo, args.runs, args.seed
            if extra is not None and extra < 0:
                raise ProfileError("--extra cannot be negative.")

        findings = run(profile, sections, extra, mc, runs, seed)
        print(render_json(findings) if as_json else render_text(findings, color))
        return 0
    except (ProfileError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\naborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
