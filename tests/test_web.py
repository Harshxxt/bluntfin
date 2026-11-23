import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from bluntfin.countries import COUNTRIES, apply_country
from bluntfin.model import profile_from_dict
from bluntfin.server import Handler


class Countries(unittest.TestCase):
    def test_every_country_builds_a_valid_profile(self):
        for code in COUNTRIES:
            data = apply_country({"country": code, "net_monthly_income": 3000, "gross_annual_income": 50000,
                                  "monthly_expenses": {"housing": 1000}, "currency": "€"})
            p = profile_from_dict(data)
            if code != "OTHER":
                self.assertEqual(p.currency, COUNTRIES[code]["currency"])
                self.assertIsNotNone(p.tax)

    def test_country_currency_cannot_be_spoofed(self):
        self.assertEqual(apply_country({"country": "IN", "currency": "$"})["currency"], "₹")

    def test_us_filing_passthrough(self):
        d = apply_country({"country": "US", "tax": {"filing": "married", "state_tax_pct": 5}})
        self.assertEqual(d["tax"], {"system": "us", "filing": "married", "state_tax_pct": 5})

    def test_unknown_country(self):
        with self.assertRaises(ValueError):
            apply_country({"country": "ZZ"})


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.log_message = lambda *a: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, body: bytes):
        req = urllib.request.Request(self.base + "/api/analyze", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_index_served(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"bluntfin", r.read())

    def test_analyze(self):
        body = {"country": "IN", "net_monthly_income": 105000, "gross_annual_income": 1500000, "age": 27,
                "monthly_expenses": {"housing": 28000}, "_options": {"runs": 200, "seed": 1}}
        status, data = self.post(json.dumps(body).encode())
        self.assertEqual(status, 200)
        self.assertEqual(data["currency"], "₹")
        self.assertTrue(any(f["section"] == "tax" for f in data["findings"]))

    def test_validation_error_is_400(self):
        status, data = self.post(b'{"country": "US"}')
        self.assertEqual(status, 400)
        self.assertTrue(data["error"])

    def test_bad_json_is_400(self):
        self.assertEqual(self.post(b"{nope")[0], 400)

    def test_runs_capped(self):
        body = {"net_monthly_income": 1, "monthly_expenses": {"a": 1}, "_options": {"runs": 10_000_000}}
        self.assertEqual(self.post(json.dumps(body).encode())[0], 400)


if __name__ == "__main__":
    unittest.main()
