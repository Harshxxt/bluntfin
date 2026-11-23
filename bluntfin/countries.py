"""Country presets: currency, number format, tax system and local vocabulary.

Only countries with a real, verified tax implementation are listed. "other" lets
the user supply their own currency and brackets instead of being given fake
numbers for a country we do not model.
"""
from __future__ import annotations

COUNTRIES = {
    "US": {
        "name": "United States",
        "currency": "$",
        "currency_code": "USD",
        "locale": "en-US",
        "number_grouping": "western",
        "tax": {"system": "us"},
        "tax_label": "US federal 2026 + FICA (state as a flat %)",
        "options": ["filing", "state_tax_pct"],
        "pretax_label": "401(k) / 403(b) / traditional IRA contributions taken from paycheck",
        "retirement_age": 65,
        "life_expectancy": 95,
    },
    "GB": {
        "name": "United Kingdom",
        "currency": "£",
        "currency_code": "GBP",
        "locale": "en-GB",
        "number_grouping": "western",
        "tax": {"system": "uk"},
        "tax_label": "UK 2026/27 income tax (England, Wales, NI bands) + employee NI",
        "options": [],
        "pretax_label": "Workplace pension contributions taken from salary",
        "retirement_age": 67,
        "life_expectancy": 95,
    },
    "IN": {
        "name": "India",
        "currency": "₹",
        "currency_code": "INR",
        "locale": "en-IN",
        "number_grouping": "indian",
        "tax": {"system": "india_new"},
        "tax_label": "New tax regime FY 2026-27 (slabs, ₹75,000 standard deduction, 87A rebate, cess)",
        "options": [],
        "pretax_label": "EPF / NPS deducted from salary",
        "retirement_age": 60,
        "life_expectancy": 90,
    },
    "OTHER": {
        "name": "Other country (enter your own tax brackets)",
        "currency": "",
        "currency_code": "",
        "locale": "en-US",
        "number_grouping": "western",
        "tax": None,
        "tax_label": "Your own brackets. We do not guess tax rules for countries we have not implemented.",
        "options": ["currency", "custom_brackets"],
        "pretax_label": "Retirement contributions taken from salary before take-home",
        "retirement_age": 65,
        "life_expectancy": 90,
    },
}


def apply_country(data: dict) -> dict:
    """Fill currency, number format, tax system and defaults from data['country'].
    Explicit values in `data` win over country defaults."""
    code = data.get("country")
    if code is None:
        return data
    if code not in COUNTRIES:
        raise ValueError(f"Unsupported country {code!r}. Supported: {', '.join(COUNTRIES)}.")
    c = COUNTRIES[code]
    out = dict(data)
    out.setdefault("number_grouping", c["number_grouping"])
    out.setdefault("retirement_age", c["retirement_age"])
    out.setdefault("life_expectancy", c["life_expectancy"])
    if code == "OTHER":
        out.setdefault("currency", "")
        return out
    out["currency"] = c["currency"]
    tax = dict(c["tax"])
    user_tax = data.get("tax") or {}
    if code == "US":
        if "filing" in user_tax:
            tax["filing"] = user_tax["filing"]
        if "state_tax_pct" in user_tax:
            tax["state_tax_pct"] = user_tax["state_tax_pct"]
    out["tax"] = tax
    return out


def public_countries() -> dict:
    return {k: {f: v[f] for f in ("name", "currency", "currency_code", "locale", "tax_label", "options",
                                   "pretax_label", "retirement_age", "life_expectancy")}
            for k, v in COUNTRIES.items()}
