"""Income tax engine.

Built-in systems (resident individuals, employment income):
  us          US federal income tax + FICA, tax year 2026 (IRS Rev. Proc. 2025-32 figures,
              SSA wage base $184,500). Optional flat state/local rate.
  uk          UK income tax (England/Wales/NI bands) + employee Class 1 NI, 2026/27.
  india_new   India new regime, FY 2026-27: slabs, Rs 75,000 standard deduction,
              s.87A rebate with marginal relief, surcharge, 4% cess.
  custom      Your own brackets.

Deliberate simplifications (stated, not hidden):
  - Wages only. No capital gains, dividends, credits (child tax credit, EITC), itemizing.
  - US: household treated as one earner for the Social Security cap; state tax is a flat
    rate on federal taxable income.
  - UK: Scotland's bands are not modelled. Pension contributions assumed net-pay
    (reduce income tax, not NI).
  - India: surcharge applied without marginal relief, so it slightly overstates tax just
    above Rs 50L / 1Cr / 2Cr. Employee EPF is not deductible under the new regime.
  - Future years use today's brackets (i.e. brackets assumed to rise with inflation).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

Brackets = list[tuple[float | None, float]]  # (upper limit of taxable income or None, rate %)


class TaxConfigError(ValueError):
    pass


@dataclass
class TaxResult:
    gross: float
    income_tax: float
    payroll_tax: float

    @property
    def total(self) -> float:
        return self.income_tax + self.payroll_tax

    @property
    def effective_pct(self) -> float:
        return 100 * self.total / self.gross if self.gross > 0 else 0.0


@dataclass
class TaxSystem:
    key: str
    label: str
    pretax_deductible: bool            # do payroll retirement contributions cut income tax?
    default_pretax_fraction: float     # share of retirement withdrawals taxed as income
    _fn: Callable[[float, float, bool], tuple[float, float]]

    def compute(self, gross: float, pretax: float = 0.0, wages: bool = True) -> TaxResult:
        """Annual tax on `gross`. `pretax` = annual pre-tax retirement contributions.
        wages=False means retirement income (no payroll tax)."""
        gross = max(gross, 0.0)
        pretax = min(max(pretax, 0.0), gross) if self.pretax_deductible else 0.0
        income, payroll = self._fn(gross, pretax, wages)
        return TaxResult(gross, max(income, 0.0), max(payroll, 0.0) if wages else 0.0)

    def marginal_pct(self, gross: float, pretax: float = 0.0, wages: bool = True, step: float = 100.0) -> float:
        """Tax on the next `step` of income, as a percent. Captures cliffs and tapers."""
        a = self.compute(gross, pretax, wages).total
        b = self.compute(gross + step, pretax, wages).total
        return 100 * (b - a) / step

    def gross_up(self, net_needed: float, taxable_fraction: float) -> float:
        """Withdrawal W such that W - tax(W * taxable_fraction) == net_needed (retirement income)."""
        if net_needed <= 0:
            return 0.0
        lo, hi = net_needed, net_needed * 3 + 1
        while hi - self.compute(hi * taxable_fraction, wages=False).total < net_needed:
            hi *= 2
        for _ in range(60):
            mid = (lo + hi) / 2
            if mid - self.compute(mid * taxable_fraction, wages=False).total < net_needed:
                lo = mid
            else:
                hi = mid
        return hi


def bracket_tax(taxable: float, brackets: Brackets) -> float:
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        if taxable <= lower:
            break
        top = taxable if upper is None else min(taxable, upper)
        tax += (top - lower) * rate / 100
        if upper is None:
            break
        lower = upper
    return tax


# ------------------------------------------------------------------ US 2026

US_2026 = {
    "single": {
        "deduction": 16_100,
        "brackets": [(12_400, 10), (50_400, 12), (105_700, 22), (201_775, 24),
                     (256_225, 32), (640_600, 35), (None, 37)],
        "addl_medicare_threshold": 200_000,
    },
    "married": {
        "deduction": 32_200,
        "brackets": [(24_800, 10), (100_800, 12), (211_400, 22), (403_550, 24),
                     (512_450, 32), (768_700, 35), (None, 37)],
        "addl_medicare_threshold": 250_000,
    },
}
SS_WAGE_BASE_2026 = 184_500


def _us(filing: str, state_pct: float):
    t = US_2026[filing]

    def fn(gross, pretax, wages):
        taxable = max(gross - pretax - t["deduction"], 0.0)
        income = bracket_tax(taxable, t["brackets"]) + taxable * state_pct / 100
        payroll = 0.0
        if wages:
            payroll = (0.062 * min(gross, SS_WAGE_BASE_2026) + 0.0145 * gross
                       + 0.009 * max(gross - t["addl_medicare_threshold"], 0.0))
        return income, payroll
    return fn


# ------------------------------------------------------------------ UK 2026/27

UK_PA, UK_TAPER_START, UK_BASIC_BAND, UK_ADDITIONAL = 12_570, 100_000, 37_700, 125_140
UK_NI_PT, UK_NI_UEL = 12_570, 50_270


def _uk(gross, pretax, wages):
    adj = gross - pretax
    pa = max(UK_PA - max(adj - UK_TAPER_START, 0.0) / 2, 0.0)
    taxable = max(adj - pa, 0.0)
    income = bracket_tax(taxable, [(UK_BASIC_BAND, 20), (UK_ADDITIONAL, 40), (None, 45)])
    ni = 0.0
    if wages:
        ni = 0.08 * max(min(gross, UK_NI_UEL) - UK_NI_PT, 0.0) + 0.02 * max(gross - UK_NI_UEL, 0.0)
    return income, ni


# ------------------------------------------------------------ India FY 2026-27

IN_SLABS = [(400_000, 0), (800_000, 5), (1_200_000, 10), (1_600_000, 15),
            (2_000_000, 20), (2_400_000, 25), (None, 30)]
IN_STD_DEDUCTION, IN_REBATE_LIMIT = 75_000, 1_200_000


def _india_new(gross, pretax, wages):
    taxable = max(gross - IN_STD_DEDUCTION, 0.0)
    tax = bracket_tax(taxable, IN_SLABS)
    if taxable <= IN_REBATE_LIMIT:
        tax = 0.0
    else:
        tax = min(tax, taxable - IN_REBATE_LIMIT)  # marginal relief on the 87A rebate
    if taxable > 20_000_000:
        sur = 0.25
    elif taxable > 10_000_000:
        sur = 0.15
    elif taxable > 5_000_000:
        sur = 0.10
    else:
        sur = 0.0
    return tax * (1 + sur) * 1.04, 0.0


# ------------------------------------------------------------------- loading

def _parse_brackets(raw) -> Brackets:
    if not isinstance(raw, list) or not raw:
        raise TaxConfigError("custom tax needs 'brackets': [[upper_limit, rate_pct], ..., [null, top_rate_pct]].")
    out: Brackets = []
    prev = 0.0
    for i, item in enumerate(raw):
        if not (isinstance(item, list) and len(item) == 2):
            raise TaxConfigError(f"brackets[{i}] must be [upper_limit_or_null, rate_pct].")
        upper, rate = item
        if not isinstance(rate, (int, float)) or not 0 <= rate <= 100:
            raise TaxConfigError(f"brackets[{i}] rate must be 0-100.")
        if upper is None:
            if i != len(raw) - 1:
                raise TaxConfigError("Only the last bracket may have a null (unlimited) upper limit.")
        elif not isinstance(upper, (int, float)) or upper <= prev:
            raise TaxConfigError(f"brackets[{i}] upper limits must be increasing numbers.")
        else:
            prev = float(upper)
        out.append((None if upper is None else float(upper), float(rate)))
    if out[-1][0] is not None:
        raise TaxConfigError("The last bracket must have a null upper limit (the top rate).")
    return out


def _pct(cfg: dict, key: str, default: float = 0.0) -> float:
    v = cfg.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
        raise TaxConfigError(f"tax.{key} must be a percentage between 0 and 100.")
    return float(v)


def tax_system_from_config(cfg: dict | None) -> TaxSystem | None:
    if cfg is None:
        return None
    if not isinstance(cfg, dict) or "system" not in cfg:
        raise TaxConfigError("'tax' must be an object with a 'system': us, uk, india_new or custom.")
    system = cfg["system"]
    if system == "us":
        filing = cfg.get("filing", "single")
        if filing not in US_2026:
            raise TaxConfigError("tax.filing must be 'single' or 'married' (married filing jointly).")
        state = _pct(cfg, "state_tax_pct")
        label = f"US federal 2026 ({filing})" + (f" + {state:g}% state" if state else "")
        return TaxSystem("us", label, True, 1.0, _us(filing, state))
    if system == "uk":
        return TaxSystem("uk", "UK 2026/27 (rUK bands) + NI", True, 0.75, _uk)
    if system == "india_new":
        return TaxSystem("india_new", "India new regime FY 2026-27", False, 0.0, _india_new)
    if system == "custom":
        brackets = _parse_brackets(cfg.get("brackets"))
        deduction = cfg.get("deduction", 0)
        if isinstance(deduction, bool) or not isinstance(deduction, (int, float)) or deduction < 0:
            raise TaxConfigError("tax.deduction must be a non-negative number.")
        payroll_pct = _pct(cfg, "payroll_pct")

        def fn(gross, pretax, wages):
            taxable = max(gross - pretax - deduction, 0.0)
            return bracket_tax(taxable, brackets), gross * payroll_pct / 100 if wages else 0.0
        return TaxSystem("custom", "custom brackets", bool(cfg.get("pretax_deductible", True)), 1.0, fn)
    raise TaxConfigError(f"Unknown tax system {system!r}. Use us, uk, india_new or custom.")
