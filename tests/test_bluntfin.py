import json
import unittest
from pathlib import Path

from bluntfin.analysis import (Severity, analyze_cashflow, analyze_debt, analyze_emergency,
                               analyze_goals, analyze_retirement, future_value, required_monthly,
                               run, simulate_payoff)
from bluntfin.cli import main
from bluntfin.model import Debt, ProfileError, profile_from_dict

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def base(**over):
    d = {"net_monthly_income": 5000, "monthly_expenses": {"housing": 1500, "food": 500}}
    d.update(over)
    return profile_from_dict(d)


def worst(findings):
    return min(f.severity for f in findings)


class Validation(unittest.TestCase):
    def test_missing_income(self):
        with self.assertRaises(ProfileError):
            profile_from_dict({"monthly_expenses": {"a": 1}})

    def test_empty_expenses_rejected(self):
        with self.assertRaises(ProfileError):
            profile_from_dict({"net_monthly_income": 1, "monthly_expenses": {}})

    def test_negative_rejected(self):
        with self.assertRaises(ProfileError):
            base(cash=-5)

    def test_net_above_gross_rejected(self):
        with self.assertRaises(ProfileError):
            base(gross_annual_income=12000)

    def test_bad_debt_kind(self):
        with self.assertRaises(ProfileError):
            base(debts=[{"balance": 1, "apr": 1, "min_payment": 1, "kind": "loan shark"}])


class Math(unittest.TestCase):
    def test_future_value_zero_rate(self):
        self.assertAlmostEqual(future_value(1000, 100, 0, 12), 2200)

    def test_future_value_matches_closed_form(self):
        # $100/mo for 12 months at 12%/yr (1%/mo) annuity factor 12.6825
        self.assertAlmostEqual(future_value(0, 100, 12, 12), 1268.25, places=2)

    def test_required_monthly_inverts_future_value(self):
        need = required_monthly(5000, 500000, 6, 360)
        self.assertAlmostEqual(future_value(5000, need, 6, 360), 500000, places=2)

    def test_avalanche_never_costs_more_than_snowball(self):
        debts = [Debt("a", 5000, 25, 100), Debt("b", 1000, 5, 50), Debt("c", 3000, 15, 75)]
        a = simulate_payoff(debts, 600, "avalanche")
        s = simulate_payoff(debts, 600, "snowball")
        self.assertLessEqual(a["interest"], s["interest"])

    def test_single_zero_interest_debt(self):
        r = simulate_payoff([Debt("x", 1000, 0, 100)], 100, "avalanche")
        self.assertEqual(r["months"], 10)
        self.assertEqual(r["interest"], 0)

    def test_budget_below_minimums_is_infeasible(self):
        r = simulate_payoff([Debt("x", 1000, 10, 100)], 50, "avalanche")
        self.assertFalse(r["feasible"])

    def test_negative_amortization_never_ends(self):
        r = simulate_payoff([Debt("x", 10000, 30, 200)], 200, "avalanche", rollover=False)
        self.assertFalse(r["feasible"])


class Verdicts(unittest.TestCase):
    def test_overspending_is_critical(self):
        p = base(net_monthly_income=1500)
        self.assertEqual(worst(analyze_cashflow(p)), Severity.CRITICAL)

    def test_high_savings_is_good(self):
        p = base(net_monthly_income=10000)
        self.assertEqual(analyze_cashflow(p)[0].severity, Severity.GOOD)

    def test_inconsistent_investing_flagged(self):
        p = base(net_monthly_income=2100, monthly_investing=500)
        self.assertTrue(any("do not add up" in f.headline for f in analyze_cashflow(p)))

    def test_tiny_cash_is_critical(self):
        self.assertEqual(analyze_emergency(base(cash=500))[0].severity, Severity.CRITICAL)

    def test_cash_hoarding_with_card_debt(self):
        p = base(cash=60000, debts=[{"balance": 5000, "apr": 24, "min_payment": 150, "kind": "credit_card"}])
        self.assertIn("hoard", analyze_emergency(p)[0].headline)

    def test_minimum_below_interest_is_critical(self):
        p = base(debts=[{"name": "card", "balance": 10000, "apr": 30, "min_payment": 200, "kind": "credit_card"}])
        self.assertTrue(any(f.severity == Severity.CRITICAL and "grows" in f.headline for f in analyze_debt(p)))

    def test_retirement_needs_age(self):
        self.assertIn("No age", analyze_retirement(base())[0].headline)

    def test_retirement_on_track(self):
        p = base(age=30, investments=2_000_000)
        self.assertEqual(analyze_retirement(p)[0].severity, Severity.GOOD)

    def test_unaffordable_goal(self):
        p = base(goals=[{"name": "boat", "target": 200000, "months": 12}])
        self.assertEqual(analyze_goals(p)[0].severity, Severity.BAD)

    def test_section_filter(self):
        f = run(base(), ["cashflow"])
        self.assertEqual({x.section for x in f}, {"cashflow"})

    def test_unknown_section(self):
        with self.assertRaises(ValueError):
            run(base(), ["horoscope"])


class Cli(unittest.TestCase):
    def test_examples_run_and_emit_json(self):
        import contextlib, io
        for ex in EXAMPLES.glob("*.json"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(main(["analyze", str(ex), "--json"]), 0)
            data = json.loads(buf.getvalue())
            self.assertIn("findings", data)

    def test_bad_file_exit_code(self):
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["analyze", "does-not-exist.json"]), 2)


if __name__ == "__main__":
    unittest.main()
