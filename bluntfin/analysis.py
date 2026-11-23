"""The analysis engine. Every function takes a Profile and returns Findings.

Thresholds are mainstream rules of thumb (stated in each docstring). They are
not laws of nature; they are the bar most planners and lenders use. The
verdicts are blunt on purpose. They describe the numbers, not the person.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from .model import Debt, Profile
from .montecarlo import Simulator

MAX_MONTHS = 1200  # 100 years: if payoff takes longer, it never happens


class Severity(IntEnum):
    CRITICAL = 0
    BAD = 1
    WEAK = 2
    OK = 3
    GOOD = 4


@dataclass
class Finding:
    section: str
    severity: Severity
    headline: str
    detail: str = ""
    metrics: dict = field(default_factory=dict)


def money(profile: Profile, x: float) -> str:
    sign = "-" if x < 0 else ""
    n = round(abs(x))
    if profile.number_grouping == "indian" and n >= 1000:
        s = str(n)
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        body = ",".join(groups) + "," + tail
    else:
        body = f"{n:,}"
    return f"{sign}{profile.currency}{body}"


def pct(x: float) -> str:
    return f"{x:.1f}%"


# ---------------------------------------------------------------- cash flow

def analyze_cashflow(p: Profile) -> list[Finding]:
    """Savings rate bands: <0 critical, <5% bad, <10% weak, <20% ok, >=20% good."""
    out: list[Finding] = []
    s = p.surplus
    # Savings rate includes payroll retirement contributions (they are savings you never see).
    denom = p.net_monthly_income + p.payroll_savings_monthly
    rate = 100 * (s + p.payroll_savings_monthly) / denom if denom > 0 else -100.0
    metrics = {
        "net_monthly_income": p.net_monthly_income,
        "living_expenses": p.total_expenses,
        "debt_minimums": p.debt_minimums,
        "monthly_surplus": s,
        "savings_rate_pct": round(rate, 2),
    }

    if p.net_monthly_income == 0:
        out.append(Finding("cashflow", Severity.CRITICAL, "You have no income.",
                           f"You burn {money(p, -s)} a month with nothing coming in. "
                           f"At this rate your cash lasts {_runway(p)}. Income is the only priority.", metrics))
    elif s < 0:
        out.append(Finding("cashflow", Severity.CRITICAL,
                           f"You spend more than you earn: {money(p, -s)} short every month.",
                           f"That is {money(p, -s * 12)} a year funded by savings or debt. "
                           f"Your cash lasts {_runway(p)}. Every other number in this report is "
                           "irrelevant until this is positive.", metrics))
    elif rate < 5:
        out.append(Finding("cashflow", Severity.BAD, f"Savings rate {pct(rate)}. You are living paycheck to paycheck.",
                           "One car repair or a lost job and you are in debt. The minimum acceptable is 10%; "
                           "the standard target is 20%.", metrics))
    elif rate < 10:
        out.append(Finding("cashflow", Severity.WEAK, f"Savings rate {pct(rate)}. Too thin.",
                           f"Getting to 20% means freeing up {money(p, _gap_to_rate(p, 20))} a month.", metrics))
    elif rate < 20:
        out.append(Finding("cashflow", Severity.OK, f"Savings rate {pct(rate)}. Adequate, not strong.",
                           f"Getting to 20% means freeing up {money(p, _gap_to_rate(p, 20))} a month.", metrics))
    else:
        out.append(Finding("cashflow", Severity.GOOD, f"Savings rate {pct(rate)}. Strong.",
                           "Make sure the surplus is actually invested or applied to debt, not just sitting "
                           "in checking and quietly being spent.", metrics))

    # Where the money goes
    if p.net_monthly_income > 0:
        top = sorted(p.monthly_expenses.items(), key=lambda kv: kv[1], reverse=True)[:3]
        lines = [f"{k}: {money(p, v)} ({pct(100 * v / p.net_monthly_income)} of take-home)" for k, v in top]
        out.append(Finding("cashflow", Severity.OK, "Your three biggest expenses.",
                           "; ".join(lines) + ". If you want to cut, cut here; trimming small categories "
                           "is mostly theater.", {"top_expenses": dict(top)}))

    # Housing burden
    if p.housing_cost > 0:
        if p.gross_monthly_income:
            h = 100 * p.housing_cost / p.gross_monthly_income
            base, limit = "gross", 28
        elif p.net_monthly_income > 0:
            h = 100 * p.housing_cost / p.net_monthly_income
            base, limit = "take-home", 35
        else:
            h = None
        if h is not None:
            m = {"housing_cost": p.housing_cost, f"housing_pct_of_{base}": round(h, 2)}
            if h > limit + 15:
                out.append(Finding("cashflow", Severity.BAD, f"Housing eats {pct(h)} of {base} income.",
                                   f"The standard ceiling is {limit}%. You are house-poor, and it is the "
                                   "single largest lever you have.", m))
            elif h > limit:
                out.append(Finding("cashflow", Severity.WEAK, f"Housing is {pct(h)} of {base} income.",
                                   f"Above the {limit}% guideline. Not a crisis, but it caps everything else.", m))

    # Consistency check: claimed investing vs. reality
    if p.monthly_investing > max(s, 0) + 0.5:
        out.append(Finding("cashflow", Severity.BAD,
                           "Your numbers do not add up.",
                           f"You say you invest {money(p, p.monthly_investing)} a month, but your surplus after "
                           f"expenses and debt is {money(p, s)}. Either your expenses are understated or that "
                           "investing is funded by debt or savings. Track real spending for 60 days, then rerun.",
                           {"claimed_investing": p.monthly_investing, "monthly_surplus": s}))
    return out


def _runway(p: Profile) -> str:
    burn = -p.surplus
    if burn <= 0:
        return "indefinitely"
    months = p.cash / burn
    return f"{months:.1f} months" if months >= 1 else f"{months * 30:.0f} days"


def _gap_to_rate(p: Profile, target: float) -> float:
    denom = p.net_monthly_income + p.payroll_savings_monthly
    needed = target / 100 * denom - p.payroll_savings_monthly
    return max(needed - p.surplus, 0.0)


# ----------------------------------------------------------- emergency fund

def analyze_emergency(p: Profile) -> list[Finding]:
    """Bands in months of essential outflow: <1 critical, <3 bad, <6 ok, >=6 good."""
    outflow = p.total_expenses + p.debt_minimums
    if outflow <= 0:
        return []
    months = p.cash / outflow
    m = {"cash": p.cash, "monthly_outflow": outflow, "months_covered": round(months, 2)}
    high_interest = [d for d in p.debts if d.apr >= 10 and d.kind != "mortgage" and d.balance > 0]

    if months < 1:
        f = Finding("emergency", Severity.CRITICAL, f"Your cash covers {months * 30:.0f} days of expenses.",
                    "Any surprise goes straight on a credit card. Build one month of cash before anything "
                    f"else except minimum payments. Target: {money(p, outflow)}.", m)
    elif months < 3:
        f = Finding("emergency", Severity.BAD, f"Emergency fund: {months:.1f} months.",
                    f"A job loss would wipe you out. Minimum target is 3 months ({money(p, 3 * outflow)}).", m)
    elif months < 6:
        f = Finding("emergency", Severity.OK, f"Emergency fund: {months:.1f} months.",
                    f"Acceptable. 6 months ({money(p, 6 * outflow)}) if your income is variable or you "
                    "are the sole earner.", m)
    elif months > 12 and high_interest:
        worst = max(high_interest, key=lambda d: d.apr)
        f = Finding("emergency", Severity.WEAK, f"You hoard {months:.0f} months of cash while carrying "
                    f"{pct(worst.apr)} debt.",
                    f"Cash earns a few percent; '{worst.name}' costs {pct(worst.apr)}. Keep 6 months and throw "
                    f"the excess ({money(p, p.cash - 6 * outflow)}) at the debt. This is a guaranteed return.", m)
    elif months > 12:
        f = Finding("emergency", Severity.OK, f"Emergency fund: {months:.0f} months. Overkill.",
                    f"Anything beyond 6-12 months is losing to inflation. Consider investing "
                    f"{money(p, p.cash - 12 * outflow)}.", m)
    else:
        f = Finding("emergency", Severity.GOOD, f"Emergency fund: {months:.1f} months. Solid.", "", m)
    return [f]


# --------------------------------------------------------------------- debt

def simulate_payoff(debts: list[Debt], monthly_budget: float, strategy: str, rollover: bool = True) -> dict:
    """Month-by-month payoff. With rollover, freed minimums go to the next target;
    without it, each debt only ever gets its own minimum (budget is ignored).

    strategy: 'avalanche' (highest APR first, minimizes interest) or
              'snowball' (smallest balance first, fastest early wins).
    """
    active = [d for d in debts if d.balance > 0]
    if not active:
        return {"months": 0, "interest": 0.0, "feasible": True}
    if strategy == "avalanche":
        order = sorted(range(len(active)), key=lambda i: (-active[i].apr, active[i].balance))
    elif strategy == "snowball":
        order = sorted(range(len(active)), key=lambda i: (active[i].balance, -active[i].apr))
    else:
        raise ValueError(f"unknown strategy {strategy!r}")

    if monthly_budget + 1e-9 < sum(d.min_payment for d in active):
        return {"months": None, "interest": None, "feasible": False}

    bal = [d.balance for d in active]
    interest = 0.0
    month = 0
    while any(b > 0.005 for b in bal):
        month += 1
        if month > MAX_MONTHS:
            return {"months": None, "interest": None, "feasible": False}
        for i, d in enumerate(active):
            if bal[i] > 0:
                charge = bal[i] * d.apr / 1200
                bal[i] += charge
                interest += charge
        avail = monthly_budget
        for i, d in enumerate(active):
            pay = min(bal[i], d.min_payment)
            bal[i] -= pay
            avail -= pay
        for i in (order if rollover else []):
            if avail <= 0:
                break
            pay = min(bal[i], avail)
            bal[i] -= pay
            avail -= pay
    return {"months": month, "interest": round(interest, 2), "feasible": True}


def analyze_debt(p: Profile, extra: float | None = None) -> list[Finding]:
    """Flags negative amortization, high-APR debt, DTI (lender bands 36/43/50), payoff plans."""
    out: list[Finding] = []
    if not p.debts:
        return [Finding("debt", Severity.GOOD, "No debt.", "Keep it that way.")]

    # Minimum payment does not even cover interest
    for d in p.debts:
        if d.balance > 0 and d.min_payment <= d.monthly_interest:
            out.append(Finding("debt", Severity.CRITICAL, f"'{d.name}' grows every month.",
                               f"Interest is {money(p, d.monthly_interest)}/mo, your payment is "
                               f"{money(p, d.min_payment)}. Paying the minimum, this debt is never paid off.",
                               {"debt": d.name, "monthly_interest": round(d.monthly_interest, 2)}))

    consumer = [d for d in p.debts if d.kind != "mortgage" and d.balance > 0]
    toxic = [d for d in consumer if d.apr >= 15]
    if toxic:
        total = sum(d.balance for d in toxic)
        yearly = sum(d.balance * d.apr / 100 for d in toxic)
        out.append(Finding("debt", Severity.BAD,
                           f"{money(p, total)} of debt at 15%+ APR, costing about {money(p, yearly)} a year.",
                           "No investment reliably beats this. Paying it off is a guaranteed 15%+ return.",
                           {"high_apr_balance": total, "annual_interest_cost": round(yearly, 2)}))
        if p.monthly_investing > 0 or p.investments > 0:
            worst = max(toxic, key=lambda d: d.apr)
            out.append(Finding("debt", Severity.WEAK, "You are investing while paying credit-card-level interest.",
                               f"Markets average ~7% real at best; '{worst.name}' charges {pct(worst.apr)}. "
                               "Beyond any employer match, redirect investing to this debt.", {}))

    # Debt-to-income (back-end): all debt payments + rent over gross
    if p.gross_monthly_income:
        rent = p.monthly_expenses.get(p.housing_category, 0.0)
        dti = 100 * (p.debt_minimums + rent) / p.gross_monthly_income
        m = {"dti_pct": round(dti, 2)}
        if dti > 50:
            out.append(Finding("debt", Severity.CRITICAL, f"Debt-to-income {pct(dti)}.",
                               "Above 50%, no mainstream lender will touch you, and you are one bad month from "
                               "default.", m))
        elif dti > 43:
            out.append(Finding("debt", Severity.BAD, f"Debt-to-income {pct(dti)}.",
                               "Above the 43% qualified-mortgage ceiling. You are over-leveraged.", m))
        elif dti > 36:
            out.append(Finding("debt", Severity.WEAK, f"Debt-to-income {pct(dti)}.",
                               "Above the 36% comfort line lenders use.", m))
        else:
            out.append(Finding("debt", Severity.OK, f"Debt-to-income {pct(dti)}. Within lender norms.", "", m))

    # Payoff plans (non-mortgage)
    if consumer:
        if extra is None:
            extra = max(p.surplus - p.monthly_investing, 0.0)
        budget = sum(d.min_payment for d in consumer) + extra
        mins_only = simulate_payoff(consumer, sum(d.min_payment for d in consumer), "avalanche", rollover=False)
        aval = simulate_payoff(consumer, budget, "avalanche")
        snow = simulate_payoff(consumer, budget, "snowball")
        m = {"extra_per_month": round(extra, 2), "minimums_only": mins_only,
             "avalanche": aval, "snowball": snow}
        if not aval["feasible"]:
            out.append(Finding("debt", Severity.CRITICAL, "At your current payments, your debt is never paid off.",
                               "You need more income, lower expenses, or a negotiated hardship plan. "
                               "This is not optional.", m))
        else:
            first = sorted(consumer, key=lambda d: -d.apr)[0]
            detail = (f"Putting {money(p, extra)}/mo extra on top of minimums: "
                      f"avalanche (highest APR first, start with '{first.name}') = debt-free in "
                      f"{_dur(aval['months'])}, {money(p, aval['interest'])} interest. "
                      f"Snowball = {_dur(snow['months'])}, {money(p, snow['interest'])} interest.")
            if mins_only["feasible"]:
                detail += (f" Minimums only = {_dur(mins_only['months'])}, "
                           f"{money(p, mins_only['interest'])} interest.")
            else:
                detail += " Minimums only = never."
            if snow["feasible"] and snow["interest"] - aval["interest"] >= 1:
                detail += (f" Snowball costs {money(p, snow['interest'] - aval['interest'])} more; pick it only "
                           "if you have already failed at avalanche.")
            if p.surplus < 0:
                detail = (f"Warning: you are {money(p, -p.surplus)}/mo short already, so even the minimums are "
                          "being paid from savings or new debt. This plan only works once cash flow is fixed. "
                          + detail)
            sev = Severity.OK if aval["months"] <= 36 else Severity.WEAK if aval["months"] <= 84 else Severity.BAD
            out.append(Finding("debt", sev, f"Debt-free in {_dur(aval['months'])} if you follow the plan.",
                               detail, m))
    return out


def _dur(months: int | None) -> str:
    if months is None:
        return "never"
    y, mo = divmod(months, 12)
    if y == 0:
        return f"{mo} mo"
    return f"{y} yr" + (f" {mo} mo" if mo else "")


# ---------------------------------------------------------------- net worth

def analyze_networth(p: Profile) -> list[Finding]:
    """Benchmark: 'expected net worth' = age x gross income / 10 (Stanley & Danko).
    Crude, but widely used. Ignored if age or gross income are missing."""
    nw = p.assets - p.liabilities
    m = {"assets": p.assets, "liabilities": p.liabilities, "net_worth": nw}
    if nw < 0:
        return [Finding("networth", Severity.BAD, f"Net worth {money(p, nw)}.",
                        "You owe more than you own. If you sold everything today, you would still be in debt.", m)]
    if p.age and p.gross_annual_income:
        bench = p.age * p.gross_annual_income / 10
        ratio = nw / bench if bench else 0
        m.update({"benchmark": bench, "ratio_to_benchmark": round(ratio, 2)})
        if ratio < 0.5:
            sev, verdict = Severity.WEAK, "well below where your income says you should be. High earner, low keeper."
        elif ratio < 1:
            sev, verdict = Severity.OK, "below benchmark."
        else:
            sev, verdict = Severity.GOOD, "at or above benchmark."
        return [Finding("networth", sev, f"Net worth {money(p, nw)}: {verdict}",
                        f"Benchmark for your age and income: {money(p, bench)} (age x income / 10). "
                        "Ignore this if you are under 30 or just finished school; it is a crude yardstick.", m)]
    return [Finding("networth", Severity.OK, f"Net worth {money(p, nw)}.",
                    "Provide age and gross_annual_income for a benchmark.", m)]


# --------------------------------------------------------------- retirement

def future_value(pv: float, monthly: float, annual_rate_pct: float, months: int) -> float:
    r = annual_rate_pct / 1200
    if months <= 0:
        return pv
    if abs(r) < 1e-12:
        return pv + monthly * months
    g = (1 + r) ** months
    return pv * g + monthly * (g - 1) / r


def required_monthly(pv: float, target: float, annual_rate_pct: float, months: int) -> float:
    r = annual_rate_pct / 1200
    if months <= 0:
        return float("inf") if pv < target else 0.0
    if abs(r) < 1e-12:
        return max(target - pv, 0) / months
    g = (1 + r) ** months
    return max((target - pv * g) * r / (g - 1), 0.0)


def retirement_withdrawal(p: Profile) -> tuple[float, float, float]:
    """(net spending, gross withdrawal needed, taxable fraction) per year, today's money."""
    spending = p.retirement_spending_annual or p.total_expenses * 12
    if not p.tax:
        return spending, spending, 0.0
    frac = p.pretax_fraction if p.pretax_fraction is not None else p.tax.default_pretax_fraction
    return spending, p.tax.gross_up(spending, frac), frac


def analyze_retirement(p: Profile, monte_carlo: bool = True, runs: int = 5000,
                       seed: int | None = None) -> list[Finding]:
    """Target = gross withdrawal / withdrawal rate (4% rule -> 25x). Real (inflation-adjusted) money.
    With monte_carlo, the verdict comes from simulated success probability, not the straight line."""
    if p.age is None:
        return [Finding("retirement", Severity.WEAK, "No age given, so no retirement projection.",
                        "Add 'age' to your profile. Not knowing is not a plan.")]
    spending, withdrawal, frac = retirement_withdrawal(p)
    target = withdrawal / (p.withdrawal_rate / 100)
    months = max(p.retirement_age - p.age, 0) * 12
    contrib = p.payroll_savings_monthly + p.monthly_investing
    projected = future_value(p.investments, contrib, p.expected_real_return, months)
    need = required_monthly(p.investments, target, p.expected_real_return, months)
    ratio = projected / target if target else 1.0
    m = {"target": round(target), "projected": round(projected), "funded_ratio": round(ratio, 2),
         "current_monthly_contribution": contrib, "required_monthly_contribution": round(need, 2),
         "years_left": months // 12, "assumed_real_return_pct": p.expected_real_return,
         "net_spending": round(spending), "gross_withdrawal": round(withdrawal)}

    assumptions = (f"Assumes {pct(p.expected_real_return)} real return, {pct(p.withdrawal_rate)} withdrawal, "
                   f"{money(p, spending)}/yr spending in today's money, retirement at {p.retirement_age}.")
    if p.tax and withdrawal > spending + 1:
        assumptions += (f" Tax ({p.tax.label}, {frac:.0%} of withdrawals taxable) means you must withdraw "
                        f"{money(p, withdrawal)}/yr to spend {money(p, spending)}.")
    out: list[Finding] = []
    if months == 0:
        sev = Severity.GOOD if ratio >= 1 else Severity.CRITICAL if ratio < 0.5 else Severity.BAD
        out.append(Finding("retirement", sev, f"At retirement age with {pct(100 * ratio)} of what you need.",
                           f"Target {money(p, target)}, have {money(p, p.investments)}. {assumptions}", m))
    else:
        head = (f"Straight-line: {money(p, projected)} by {p.retirement_age}; you need {money(p, target)} "
                f"({pct(100 * ratio)} funded).")
        gap = need - contrib
        if ratio >= 1:
            sev, detail = Severity.GOOD, f"On track if markets were smooth. {assumptions}"
        else:
            detail = (f"You invest {money(p, contrib)}/mo; you need {money(p, need)}/mo, a gap of "
                      f"{money(p, gap)}/mo. {assumptions}")
            if ratio < 0.25:
                detail = "You are not going to retire on this trajectory. " + detail
                sev = Severity.CRITICAL
            elif ratio < 0.5:
                sev = Severity.BAD
            elif ratio < 0.75:
                sev = Severity.WEAK
            else:
                sev = Severity.OK
            if gap > max(p.surplus - p.monthly_investing, 0):
                detail += (" The gap is bigger than your free cash flow, so this requires earning more or "
                           "spending less, not just 'trying harder'.")
        out.append(Finding("retirement", sev, head, detail, m))

    if monte_carlo and p.age < p.life_expectancy:
        out.append(_monte_carlo_finding(p, withdrawal, contrib, runs, seed))
        # The simulation carries the verdict; the straight line is context only.
        if out[0].severity < Severity.OK:
            out[0].severity = Severity.OK
            out[0].headline += " (see simulation below for the real verdict)"
    return out


def _monte_carlo_finding(p: Profile, withdrawal: float, contrib_monthly: float,
                         runs: int, seed: int | None) -> Finding:
    ret_age = max(p.retirement_age, p.age)
    sim = Simulator(ret_age - p.age, p.life_expectancy - ret_age, p.expected_real_return,
                    p.return_volatility, runs, seed)
    r = sim.run(p.investments, contrib_monthly * 12, withdrawal, ret_age)
    need90 = sim.contribution_for(0.90, p.investments, withdrawal, ret_age)
    sr = 100 * r.success_rate
    m = {"runs": r.runs, "success_rate_pct": round(sr, 1),
         "balance_at_retirement": {k: round(v) for k, v in r.at_retirement.items()},
         "balance_at_end": {k: round(v) for k, v in r.at_end.items()},
         "median_depletion_age": r.median_depletion_age, "p10_depletion_age": r.p10_depletion_age,
         "monthly_contribution_for_90pct": None if need90 is None else round(need90 / 12, 2),
         "return_mean_pct": p.expected_real_return, "return_stdev_pct": p.return_volatility,
         "plan_to_age": p.life_expectancy}

    sr_txt = f"{sr:.1f}" if 99 < sr < 100 or 0 < sr < 1 else f"{sr:.0f}"
    fail_txt = f"{100 - sr:.1f}" if 0 < 100 - sr < 1 or 99 < 100 - sr < 100 else f"{100 - sr:.0f}"
    head = f"Monte Carlo: you make it to {p.life_expectancy} in {sr_txt}% of {r.runs:,} simulated markets."
    parts = []
    if r.success_rate < 1 and r.median_depletion_age is not None:
        parts.append(f"In the {fail_txt}% that fail, the money typically runs out at age "
                     f"{r.median_depletion_age:.0f}" + (f"; in the worst 10% of markets, by {r.p10_depletion_age:.0f}"
                                                         if r.p10_depletion_age is not None else "") + ".")
    if p.retirement_age > p.age:
        a = r.at_retirement
        parts.append(f"Portfolio at {p.retirement_age}: bad luck (10th pct) {money(p, a['p10'])}, "
                     f"median {money(p, a['p50'])}, good luck (90th pct) {money(p, a['p90'])}.")
    if r.success_rate < 0.9:
        if need90 is None:
            parts.append("No realistic contribution gets you to 90% with this retirement age and spending. "
                         "Retire later or plan to spend less.")
        elif p.retirement_age > p.age:
            parts.append(f"For a 90% success rate you need to invest {money(p, need90 / 12)}/mo; "
                         f"you invest {money(p, contrib_monthly)}/mo.")
    parts.append(f"Returns: {pct(p.expected_real_return)} mean, {pct(p.return_volatility)} volatility, real. "
                 "A plan below ~80% is a coin flip with your old age.")

    if sr >= 90:
        sev = Severity.GOOD
    elif sr >= 75:
        sev = Severity.OK
    elif sr >= 50:
        sev = Severity.WEAK
    elif sr >= 25:
        sev = Severity.BAD
    else:
        sev = Severity.CRITICAL
    return Finding("retirement", sev, head, " ".join(parts), m)


# ---------------------------------------------------------------------- tax

def analyze_tax(p: Profile) -> list[Finding]:
    """Effective and marginal rates, take-home consistency, pre-tax contribution value."""
    if not p.tax:
        return [Finding("tax", Severity.OK, "No tax system set, so no tax analysis.",
                        "Add \"tax\": {\"system\": \"us\" | \"uk\" | \"india_new\" | \"custom\"}. "
                        "Tax is usually your single largest expense; ignoring it is a choice.")]
    gross = p.gross_annual_income or 0.0
    pretax = p.payroll_savings_monthly * 12
    t = p.tax.compute(gross, pretax)
    marginal = p.tax.marginal_pct(gross, pretax)
    implied_net = gross - t.total - pretax
    stated_net = p.net_monthly_income * 12
    m = {"system": p.tax.label, "income_tax": round(t.income_tax), "payroll_tax": round(t.payroll_tax),
         "total_tax": round(t.total), "effective_pct": round(t.effective_pct, 2),
         "marginal_pct": round(marginal, 2), "implied_net_annual": round(implied_net),
         "stated_net_annual": round(stated_net)}
    out = [Finding("tax", Severity.OK,
                   f"You pay {money(p, t.total)}/yr in tax: {pct(t.effective_pct)} effective, "
                   f"{pct(marginal)} on your next {p.currency}1.",
                   f"{p.tax.label}. Income tax {money(p, t.income_tax)}"
                   + (f", payroll tax {money(p, t.payroll_tax)}" if t.payroll_tax else "") + ". "
                   "Estimate for wage income only; no credits, capital gains or itemized deductions.", m)]

    if marginal >= 50:
        out.append(Finding("tax", Severity.WEAK, f"Your marginal rate is {pct(marginal)}. You are in a tax trap.",
                           "A cliff or allowance taper means you keep less than half of your next raise. "
                           "Pre-tax pension/retirement contributions or salary sacrifice that pull you below the "
                           "threshold are worth more here than anywhere else in your income range.", m))

    diff = stated_net - implied_net
    if gross > 0 and abs(diff) > 0.10 * implied_net:
        if diff < 0:
            detail = (f"Your gross minus estimated tax should leave about {money(p, implied_net / 12)}/mo, "
                      f"but you report {money(p, p.net_monthly_income)}/mo. {money(p, -diff / 12)}/mo is "
                      "unaccounted for: provident fund/pension deductions not entered as payroll_savings_monthly, "
                      "health insurance, other payroll deductions, or a number you got wrong. "
                      "Find out which.")
        else:
            detail = (f"You report {money(p, diff / 12)}/mo more take-home than your gross allows "
                      f"(estimate {money(p, implied_net / 12)}/mo). One of the two numbers is wrong, "
                      "or you have income this model does not know about.")
        out.append(Finding("tax", Severity.WEAK, "Your take-home pay does not match your gross pay.", detail, m))

    if p.tax.pretax_deductible and marginal > 0:
        per_k = marginal * 10  # tax saved per 1,000 contributed
        if pretax == 0:
            out.append(Finding("tax", Severity.WEAK, "You contribute nothing pre-tax.",
                               f"At your marginal rate, every {money(p, 1000)} into a pre-tax retirement account "
                               f"costs you only about {money(p, 1000 - per_k)} of take-home. You are "
                               "choosing to pay that tax. Start with whatever gets the full employer match.", m))
    return out


# -------------------------------------------------------------------- goals

def analyze_goals(p: Profile) -> list[Finding]:
    if not p.goals:
        return []
    out: list[Finding] = []
    available = max(p.surplus - p.monthly_investing, 0.0)
    total_needed = 0.0
    for g in p.goals:
        need = g.required_monthly
        total_needed += need
        m = {"goal": g.name, "required_monthly": round(need, 2), "available_monthly": round(available, 2)}
        if need == 0:
            out.append(Finding("goals", Severity.GOOD, f"'{g.name}': already funded.", "", m))
        elif need > available:
            realistic = (g.target - g.saved) / available if available > 0 else None
            when = f"about {_dur(int(realistic + 0.999))}" if realistic else "never at your current surplus"
            out.append(Finding("goals", Severity.BAD, f"'{g.name}' will not happen on your timeline.",
                               f"Needs {money(p, need)}/mo for {g.months} months; you have "
                               f"{money(p, available)}/mo free. Realistic timeline: {when}.", m))
        else:
            out.append(Finding("goals", Severity.OK, f"'{g.name}' is feasible on its own.",
                               f"Needs {money(p, need)}/mo of your {money(p, available)}/mo free cash.", m))
    if len(p.goals) > 1 and total_needed > available:
        out.append(Finding("goals", Severity.WEAK, "Your goals together are not affordable.",
                           f"They need {money(p, total_needed)}/mo combined; you have {money(p, available)}/mo. "
                           "Rank them. Wanting everything at once is how you get nothing.",
                           {"total_required": round(total_needed, 2)}))
    return out


# ---------------------------------------------------------------- top level

SECTIONS = {
    "cashflow": analyze_cashflow,
    "emergency": analyze_emergency,
    "debt": analyze_debt,
    "networth": analyze_networth,
    "retirement": analyze_retirement,
    "goals": analyze_goals,
    "tax": analyze_tax,
}


def run(p: Profile, sections: list[str] | None = None, extra_debt_payment: float | None = None,
        monte_carlo: bool = True, runs: int = 5000, seed: int | None = None) -> list[Finding]:
    chosen = sections or list(SECTIONS)
    unknown = [s for s in chosen if s not in SECTIONS]
    if unknown:
        raise ValueError(f"Unknown section(s): {', '.join(unknown)}. Choose from: {', '.join(SECTIONS)}.")
    findings: list[Finding] = []
    for name in chosen:
        if name == "debt":
            findings += analyze_debt(p, extra_debt_payment)
        elif name == "retirement":
            findings += analyze_retirement(p, monte_carlo, runs, seed)
        else:
            findings += SECTIONS[name](p)
    return findings


def priorities(findings: list[Finding]) -> list[Finding]:
    """What to fix first: CRITICAL then BAD, in a sensible order."""
    order = ["cashflow", "emergency", "debt", "tax", "retirement", "goals", "networth"]
    bad = [f for f in findings if f.severity <= Severity.BAD]
    return sorted(bad, key=lambda f: (f.severity, order.index(f.section)))
