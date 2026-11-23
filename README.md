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

## Use

```bash
bluntfin init my.json --analyze            # answer questions, get the report
bluntfin analyze my.json                   # full report
bluntfin analyze my.json --only debt,goals # only what you care about
bluntfin analyze my.json --extra 500       # what if I put $500/mo extra on debt?
bluntfin analyze my.json --json            # machine-readable output
```

Try the examples: `bluntfin analyze examples/drowning.json` and `examples/solid.json`.

## What it checks

| Section | What it measures | Bar used |
|---|---|---|
| `cashflow` | Surplus, savings rate, top expenses, housing burden, whether your claimed investing is even possible | Savings rate: <0 critical, <5% bad, <10% weak, 20%+ good. Housing ≤28% of gross |
| `emergency` | Months of expenses covered by cash; cash hoarding while carrying expensive debt | <1 mo critical, <3 bad, 3–6 ok, 6+ good |
| `debt` | Minimums that don't cover interest, 15%+ APR debt, investing while carrying it, debt-to-income, avalanche vs snowball payoff with exact months and interest | DTI 36 / 43 / 50% lender bands |
| `networth` | Assets minus liabilities vs. age × gross income ÷ 10 | Stanley & Danko benchmark (crude) |
| `retirement` | Projected portfolio vs. target (spending ÷ withdrawal rate), required monthly contribution | 4% withdrawal, 5% real return (both configurable) |
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
  "withdrawal_rate": 4.0
}
```

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
- No taxes on withdrawals, no Social Security / pensions, no income growth, no sequence-of-returns risk. The retirement projection is a straight-line estimate, not a Monte Carlo simulation.
- Debt payoff assumes fixed APR and a fixed monthly budget.
- The model is only as honest as your inputs. Underreporting spending is the most common way to fool it.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

MIT
