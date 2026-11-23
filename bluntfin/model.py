"""Input model: a user's financial profile, loaded from JSON and validated hard.

Garbage in, garbage out. Validation rejects inputs that are impossible rather
than silently "fixing" them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .tax import TaxConfigError, TaxSystem, tax_system_from_config


class ProfileError(ValueError):
    """Raised when a profile is missing data or contains impossible values."""


DEBT_KINDS = {"credit_card", "personal", "auto", "student", "mortgage", "medical", "other"}


@dataclass
class Debt:
    name: str
    balance: float
    apr: float          # annual percentage rate, in percent (22.9 means 22.9%)
    min_payment: float  # required monthly payment
    kind: str = "other"

    @property
    def monthly_interest(self) -> float:
        return self.balance * self.apr / 1200


@dataclass
class Goal:
    name: str
    target: float
    months: int         # months from now until the deadline
    saved: float = 0.0

    @property
    def required_monthly(self) -> float:
        return max(self.target - self.saved, 0.0) / self.months


@dataclass
class Profile:
    net_monthly_income: float
    monthly_expenses: dict[str, float]
    gross_annual_income: float | None = None
    age: int | None = None
    retirement_age: int = 65
    cash: float = 0.0
    investments: float = 0.0
    other_assets: float = 0.0
    payroll_savings_monthly: float = 0.0  # retirement contributions deducted before net pay
    monthly_investing: float = 0.0        # what you actually invest out of net pay each month
    debts: list[Debt] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)
    housing_category: str = "housing"
    retirement_spending_annual: float | None = None
    expected_real_return: float = 5.0     # percent, after inflation
    withdrawal_rate: float = 4.0          # percent
    currency: str = "$"
    tax: TaxSystem | None = None
    pretax_fraction: float | None = None   # share of retirement withdrawals taxed as income (0-1)
    return_volatility: float = 12.0       # annual stdev of real returns, percent
    life_expectancy: int = 95             # plan until this age
    number_grouping: str = "western"      # "western" 1,234,567 or "indian" 12,34,567

    # ---- derived values -------------------------------------------------
    @property
    def total_expenses(self) -> float:
        return sum(self.monthly_expenses.values())

    @property
    def debt_minimums(self) -> float:
        return sum(d.min_payment for d in self.debts)

    @property
    def surplus(self) -> float:
        """Net income left after living expenses and minimum debt payments."""
        return self.net_monthly_income - self.total_expenses - self.debt_minimums

    @property
    def gross_monthly_income(self) -> float | None:
        return None if self.gross_annual_income is None else self.gross_annual_income / 12

    @property
    def housing_cost(self) -> float:
        rent = self.monthly_expenses.get(self.housing_category, 0.0)
        mortgage = sum(d.min_payment for d in self.debts if d.kind == "mortgage")
        return rent + mortgage

    @property
    def assets(self) -> float:
        return self.cash + self.investments + self.other_assets

    @property
    def liabilities(self) -> float:
        return sum(d.balance for d in self.debts)


# ---- loading / validation -----------------------------------------------

def _num(data: dict, key: str, default=None, *, required=False, minimum=0.0, maximum=None):
    if key not in data or data[key] is None:
        if required:
            raise ProfileError(f"Missing required field '{key}'.")
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"'{key}' must be a number, got {value!r}.")
    if minimum is not None and value < minimum:
        raise ProfileError(f"'{key}' cannot be below {minimum}. You entered {value}.")
    if maximum is not None and value > maximum:
        raise ProfileError(f"'{key}' cannot exceed {maximum}. You entered {value}.")
    return float(value)


def profile_from_dict(data: dict) -> Profile:
    if not isinstance(data, dict):
        raise ProfileError("Profile must be a JSON object.")

    expenses = data.get("monthly_expenses")
    if not isinstance(expenses, dict) or not expenses:
        raise ProfileError("'monthly_expenses' is required: an object of category -> monthly amount. "
                           "If you think you have no expenses, you have not tracked them.")
    clean_expenses = {str(k): _num(expenses, k, required=True) for k in expenses}

    debts = []
    for i, raw in enumerate(data.get("debts", []) or []):
        if not isinstance(raw, dict):
            raise ProfileError(f"debts[{i}] must be an object.")
        kind = raw.get("kind", "other")
        if kind not in DEBT_KINDS:
            raise ProfileError(f"debts[{i}].kind must be one of {sorted(DEBT_KINDS)}, got {kind!r}.")
        debts.append(Debt(
            name=str(raw.get("name") or f"debt {i + 1}"),
            balance=_num(raw, "balance", required=True),
            apr=_num(raw, "apr", required=True, maximum=200),
            min_payment=_num(raw, "min_payment", required=True),
            kind=kind,
        ))

    goals = []
    for i, raw in enumerate(data.get("goals", []) or []):
        if not isinstance(raw, dict):
            raise ProfileError(f"goals[{i}] must be an object.")
        months = _num(raw, "months", required=True, minimum=1)
        goals.append(Goal(
            name=str(raw.get("name") or f"goal {i + 1}"),
            target=_num(raw, "target", required=True),
            months=int(months),
            saved=_num(raw, "saved", 0.0),
        ))

    age = _num(data, "age", None, minimum=14, maximum=110)
    retirement_age = _num(data, "retirement_age", 65, minimum=30, maximum=100)

    profile = Profile(
        net_monthly_income=_num(data, "net_monthly_income", required=True),
        monthly_expenses=clean_expenses,
        gross_annual_income=_num(data, "gross_annual_income", None),
        age=None if age is None else int(age),
        retirement_age=int(retirement_age),
        cash=_num(data, "cash", 0.0),
        investments=_num(data, "investments", 0.0),
        other_assets=_num(data, "other_assets", 0.0),
        payroll_savings_monthly=_num(data, "payroll_savings_monthly", 0.0),
        monthly_investing=_num(data, "monthly_investing", 0.0),
        debts=debts,
        goals=goals,
        housing_category=str(data.get("housing_category", "housing")),
        retirement_spending_annual=_num(data, "retirement_spending_annual", None),
        expected_real_return=_num(data, "expected_real_return", 5.0, minimum=-10, maximum=15),
        withdrawal_rate=_num(data, "withdrawal_rate", 4.0, minimum=0.5, maximum=15),
        currency=str(data.get("currency", "$")),
    )

    try:
        profile.tax = tax_system_from_config(data.get("tax"))
    except TaxConfigError as exc:
        raise ProfileError(str(exc)) from None
    grouping = data.get("number_grouping", "western")
    if grouping not in ("western", "indian"):
        raise ProfileError("number_grouping must be 'western' or 'indian'.")
    profile.number_grouping = grouping
    profile.pretax_fraction = _num(data, "pretax_fraction", None, maximum=1)
    profile.return_volatility = _num(data, "return_volatility", 12.0, maximum=40)
    profile.life_expectancy = int(_num(data, "life_expectancy", 95, minimum=50, maximum=120))
    if profile.life_expectancy <= profile.retirement_age:
        raise ProfileError("life_expectancy must be above retirement_age.")
    if profile.tax and profile.gross_annual_income is None:
        raise ProfileError("A 'tax' system needs 'gross_annual_income' to compute anything.")

    if profile.gross_monthly_income is not None and profile.gross_monthly_income < profile.net_monthly_income:
        raise ProfileError("Net monthly income is higher than gross monthly income. That is impossible; "
                           "one of the two numbers is wrong.")
    return profile


def load_profile(path: str | Path) -> Profile:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"Profile file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ProfileError(f"Profile is not valid JSON: {exc}") from None
    return profile_from_dict(data)
