import unittest

from bluntfin.analysis import Severity, analyze_retirement, analyze_tax
from bluntfin.model import ProfileError, profile_from_dict
from bluntfin.montecarlo import Simulator
from bluntfin.tax import TaxConfigError, bracket_tax, tax_system_from_config


def tax(cfg):
    return tax_system_from_config(cfg)


class Brackets(unittest.TestCase):
    def test_bracket_tax(self):
        b = [(10_000, 0), (20_000, 10), (None, 20)]
        self.assertEqual(bracket_tax(5_000, b), 0)
        self.assertEqual(bracket_tax(15_000, b), 500)
        self.assertEqual(bracket_tax(30_000, b), 1_000 + 2_000)

    def test_custom_validation(self):
        with self.assertRaises(TaxConfigError):
            tax({"system": "custom", "brackets": [[10000, 10], [5000, 20], [None, 30]]})
        with self.assertRaises(TaxConfigError):
            tax({"system": "custom", "brackets": [[10000, 10]]})
        with self.assertRaises(TaxConfigError):
            tax({"system": "mars"})


class US2026(unittest.TestCase):
    def test_single_58k(self):
        r = tax({"system": "us"}).compute(58_000)
        # taxable 41,900: 1,240 + 12% * 29,500 = 4,780 ; FICA 7.65% = 4,437
        self.assertAlmostEqual(r.income_tax, 4_780, places=2)
        self.assertAlmostEqual(r.payroll_tax, 4_437, places=2)

    def test_401k_cuts_income_tax_not_fica(self):
        t = tax({"system": "us"})
        a, b = t.compute(100_000), t.compute(100_000, pretax=10_000)
        self.assertAlmostEqual(a.income_tax - b.income_tax, 2_200, places=2)  # 22% bracket
        self.assertEqual(a.payroll_tax, b.payroll_tax)

    def test_social_security_cap(self):
        t = tax({"system": "us"})
        a, b = t.compute(300_000), t.compute(400_000)
        # above cap only Medicare 1.45% + 0.9% applies
        self.assertAlmostEqual(b.payroll_tax - a.payroll_tax, 100_000 * 0.0235, places=2)

    def test_married_top_bracket(self):
        self.assertAlmostEqual(tax({"system": "us", "filing": "married"}).marginal_pct(1_000_000, wages=False), 37)


class UK(unittest.TestCase):
    def test_50k(self):
        r = tax({"system": "uk"}).compute(50_000)
        self.assertAlmostEqual(r.income_tax, (50_000 - 12_570) * 0.20, places=2)
        self.assertAlmostEqual(r.payroll_tax, (50_000 - 12_570) * 0.08, places=2)

    def test_60_percent_trap(self):
        self.assertAlmostEqual(tax({"system": "uk"}).marginal_pct(110_000), 62, places=1)


class India(unittest.TestCase):
    def test_zero_tax_up_to_12_75_lakh(self):
        self.assertEqual(tax({"system": "india_new"}).compute(1_275_000).total, 0)

    def test_15_lakh(self):
        # taxable 14.25L: 20,000 + 40,000 + 33,750 = 93,750, plus 4% cess
        self.assertAlmostEqual(tax({"system": "india_new"}).compute(1_500_000).total, 97_500, places=2)

    def test_marginal_relief_cliff(self):
        t = tax({"system": "india_new"})
        self.assertAlmostEqual(t.compute(1_285_000).total, 10_000 * 1.04, places=2)
        self.assertGreaterEqual(t.marginal_pct(1_280_000), 100)


class GrossUp(unittest.TestCase):
    def test_gross_up_inverts_tax(self):
        t = tax({"system": "us"})
        w = t.gross_up(60_000, 1.0)
        self.assertAlmostEqual(w - t.compute(w, wages=False).total, 60_000, delta=0.01)

    def test_no_taxable_share_means_no_gross_up(self):
        self.assertAlmostEqual(tax({"system": "us"}).gross_up(60_000, 0.0), 60_000, delta=0.01)


class MonteCarlo(unittest.TestCase):
    def test_zero_volatility_matches_straight_line(self):
        sim = Simulator(10, 0, 5, 0, runs=100, seed=1)
        r = sim.run(1000, 0, 0, 65)
        self.assertAlmostEqual(r.at_retirement["p50"], 1000 * 1.05 ** 10, places=6)

    def test_reproducible_with_seed(self):
        a = Simulator(20, 30, 5, 12, runs=500, seed=7).run(10_000, 5_000, 20_000, 60)
        b = Simulator(20, 30, 5, 12, runs=500, seed=7).run(10_000, 5_000, 20_000, 60)
        self.assertEqual(a.success_rate, b.success_rate)

    def test_more_money_never_hurts(self):
        sim = Simulator(20, 30, 5, 15, runs=1000, seed=3)
        rates = [sim.run(0, c, 40_000, 65).success_rate for c in (0, 10_000, 20_000, 40_000)]
        self.assertEqual(rates, sorted(rates))

    def test_solver_hits_target(self):
        sim = Simulator(25, 30, 5, 12, runs=1000, seed=2)
        c = sim.contribution_for(0.9, 0, 40_000, 65)
        self.assertGreaterEqual(sim.run(0, c, 40_000, 65).success_rate, 0.9)
        self.assertLess(sim.run(0, c * 0.95, 40_000, 65).success_rate, 0.9)

    def test_no_withdrawal_always_succeeds(self):
        self.assertEqual(Simulator(5, 20, 5, 20, runs=200, seed=1).run(1, 0, 0, 65).success_rate, 1.0)


class Integration(unittest.TestCase):
    def base(self, **over):
        d = {"net_monthly_income": 6000, "gross_annual_income": 100_000, "age": 35,
             "monthly_expenses": {"housing": 2000, "food": 800}, "tax": {"system": "us"}}
        d.update(over)
        return profile_from_dict(d)

    def test_tax_requires_gross(self):
        with self.assertRaises(ProfileError):
            profile_from_dict({"net_monthly_income": 1, "monthly_expenses": {"a": 1}, "tax": {"system": "us"}})

    def test_flags_no_pretax(self):
        self.assertTrue(any("nothing pre-tax" in f.headline for f in analyze_tax(self.base())))

    def test_flags_net_mismatch(self):
        self.assertTrue(any("does not match" in f.headline for f in analyze_tax(self.base(net_monthly_income=4000))))

    def test_mc_verdict_present_and_straight_line_demoted(self):
        f = analyze_retirement(self.base(), runs=500, seed=1)
        self.assertEqual(len(f), 2)
        self.assertIn("Monte Carlo", f[1].headline)
        self.assertGreaterEqual(f[0].severity, Severity.OK)

    def test_tax_raises_retirement_target(self):
        with_tax = analyze_retirement(self.base(), monte_carlo=False)[0].metrics["target"]
        no_tax = analyze_retirement(self.base(tax=None), monte_carlo=False)[0].metrics["target"]
        self.assertGreater(with_tax, no_tax)


if __name__ == "__main__":
    unittest.main()
