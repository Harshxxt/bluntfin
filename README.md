# bluntfin

A personal financial model that tells you the truth about your money. No sugar-coating, no "great job!", no motivational filler. It reads your numbers, runs the math, and tells you what is broken and what to fix first.

Zero dependencies. Python 3.10+. Your data never leaves your machine.

```
== CASH FLOW ========================================
[CRITICAL] You spend more than you earn: $300 short every month.
           That is $3,600 a year funded by savings or debt. Your cash lasts 3.0 months.
           Every other number in this report is irrelevant until this is positive.

== DEBT ========================================
[CRITICAL] 'Visa' grows every month.
           Interest is $219/mo, your payment is $190. Paying the minimum, this debt is never paid off.

== FIX THIS, IN THIS ORDER ==========================
1. You spend more than you earn: $300 short every month.
2. Your cash covers 7 days of expenses.
3. 'Visa' grows every month.
...
```

## Install

```bash
git clone <this repo> && cd blunt-finance
pip install .
```

Or run without installing: `python -m bluntfin ...`

## Website (local)

```bash
bluntfin serve          # then open http://127.0.0.1:8000
```

Pick your country first. The site switches currency, number format (₹12,34,567 for India), tax system, retirement-account vocabulary and default retirement age to match. Nothing is stored and nothing leaves your machine: no analytics, no external scripts, and the server binds to `127.0.0.1` only.

| Country | Tax model |
|---|---|
| United States | Federal 2026 brackets + FICA (Social Security cap $184,500, Medicare + 0.9% surtax), single or married-joint, optional flat state % |
| United Kingdom | 2026/27 income tax (England/Wales/NI bands), personal-allowance taper, employee NI 8% / 2% |
| India | New regime FY 2026-27: slabs, ₹75,000 standard deduction, s.87A rebate with marginal relief, surcharge, 4% cess |
| Other | You enter your own currency and brackets. Countries without a verified implementation are not given invented numbers. |

## Command line

```bash
bluntfin init my.json --analyze            # answer questions, get the report
bluntfin analyze my.json                   # full report
bluntfin analyze my.json --only debt,goals # only what you care about
bluntfin analyze my.json --extra 500       # what if I put $500/mo extra on debt?
bluntfin analyze my.json --json            # machine-readable output
bluntfin analyze my.json --seed 1          # reproducible Monte Carlo
bluntfin analyze my.json --runs 20000      # more simulations
bluntfin analyze my.json --no-monte-carlo  # straight-line only
```

Add `"country": "US" | "GB" | "IN"` to a profile to get that country's currency and tax system automatically.

Try the examples: `examples/drowning.json`, `examples/solid.json`, `examples/india.json`.

## What it checks

| Section | What it measures | Bar used |
|---|---|---|
| `cashflow` | Surplus, savings rate, top expenses, housing burden, whether your claimed investing is even possible | Savings rate: <0 critical, <5% bad, <10% weak, 20%+ good. Housing ≤28% of gross |
| `emergency` | Months of expenses covered by cash; cash hoarding while carrying expensive debt | <1 mo critical, <3 bad, 3–6 ok, 6+ good |
| `debt` | Minimums that don't cover interest, 15%+ APR debt, investing while carrying it, debt-to-income, avalanche vs snowball payoff with exact months and interest | DTI 36 / 43 / 50% lender bands |
| `networth` | Assets minus liabilities vs. age × gross income ÷ 10 | Stanley & Danko benchmark (crude) |
| `retirement` | Straight-line projection, then a **Monte Carlo** simulation (5,000 market paths) that gives the actual verdict: probability your money lasts to your planning age, the age it runs out in failed paths, bad / median / good-luck balances, and the monthly investment needed for 90% success. Withdrawals are grossed up for tax. | Success: 90%+ good, 75% ok, 50% weak, 25% bad, below that critical |
| `tax` | Income + payroll tax, effective and **marginal** rate, tax traps (UK 60% taper, India's 12 lakh cliff), take-home vs. gross mismatch, value of pre-tax contributions | — |
| `goals` | Required monthly saving per goal vs. free cash; whether all goals together are affordable | — |

Every report ends with **FIX THIS, IN THIS ORDER**: the critical and bad findings, sorted by severity and by what has to be fixed first (cash flow before debt before retirement).

## Profile format

```json
{
  "currency": "$",
  "net_monthly_income": 3800,
  "gross_annual_income": 58000,
  "age": 31,
  "retirement_age": 65,
  "cash": 900,
  "investments": 2500,
  "other_assets": 0,
  "payroll_savings_monthly": 0,
  "monthly_investing": 200,
  "monthly_expenses": { "housing": 1650, "groceries": 450, "dining_out": 420 },
  "debts": [
    { "name": "Visa", "kind": "credit_card", "balance": 9400, "apr": 27.9, "min_payment": 190 }
  ],
  "goals": [
    { "name": "House deposit", "target": 30000, "months": 24, "saved": 0 }
  ],
  "retirement_spending_annual": null,
  "expected_real_return": 5.0,
  "withdrawal_rate": 4.0,
  "tax": { "system": "us", "filing": "single", "state_tax_pct": 4 },
  "return_volatility": 12.0,
  "life_expectancy": 95,
  "pretax_fraction": null
}
```

Tax systems: `{"system": "us", "filing": "single"|"married", "state_tax_pct": 0}`, `{"system": "uk"}`, `{"system": "india_new"}`, or `{"system": "custom", "brackets": [[10000, 0], [40000, 20], [null, 40]], "deduction": 0, "payroll_pct": 0}`. Brackets are `[upper limit of taxable income, rate %]`; the last one has a `null` limit. A tax system requires `gross_annual_income`.

`pretax_fraction` is the share of retirement withdrawals taxed as income (traditional 401(k)/IRA = 1, Roth/ISA = 0). Defaults: US 1.0, UK 0.75 (25% tax-free), India 0.

Required: `net_monthly_income`, `monthly_expenses`. Everything else is optional, but missing data means missing verdicts.

Rules that prevent garbage results:
- **Do not put debt payments in `monthly_expenses`.** They go in `debts`. Otherwise they are counted twice.
- `housing` in expenses is rent. A mortgage goes in `debts` with `"kind": "mortgage"`.
- `other_assets` is full market value (home, car). The loans against them go in `debts`.
- `payroll_savings_monthly` = retirement contributions taken out *before* your take-home pay.
- `monthly_investing` = what you actually invest *from* take-home pay. If this exceeds your surplus, the model calls out your numbers as inconsistent.
- Debt kinds: `credit_card`, `personal`, `auto`, `student`, `mortgage`, `medical`, `other`.
- Retirement math is in today's money (real returns). Default retirement spending = your current living expenses (assumes debts are gone).

## Limits (read these)

- This is a rules-of-thumb model, not licensed financial advice. The thresholds are the ones lenders and planners use; they are not universal truths.
- Tax covers wage income only: no capital gains, dividends, credits (child tax credit, EITC) or itemized deductions. US state tax is a flat %. Scotland's bands are not modelled. Future years use today's brackets.
- Monte Carlo uses independent lognormal annual returns (mean and volatility you set). Real markets have fatter tails and streaks, so treat the success rate as an upper bound. No Social Security / state pension, no income growth, constant real spending, no fees (subtract them from the return).
- Debt payoff assumes fixed APR and a fixed monthly budget.
- The model is only as honest as your inputs. Underreporting spending is the most common way to fool it.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

MIT
