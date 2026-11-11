"""Offline report-health regression tests; no live Clerk or email requests."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from taxlien import main, report
from taxlien.config import Config


def property_row(checked=True):
    return dict(case="case-1", auction_date="2026-12-01", parcel_id="parcel-1",
                address="PRIVATE PROPERTY ADDRESS", city="City", zip="00000", strap="123",
                distance_mi=1.0, opening_bid=100, market_value=1000, bid_ratio=0.1,
                elevation_ft=25, fema_zone="X", fema_subtype=None, flood_factor=1,
                flood_factor_url="https://example.org", owner="PRIVATE OWNER",
                gis_legal="LOT 1", liens={"checked": checked, "active_gov_liens": 0 if checked else None,
                                        "gov_liens": 0 if checked else None, "notes": "Screen result"}, star=False)


class ReportHealthTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(home_address="PRIVATE HOME")
        self.data = {"dates": [], "active": []}

    def test_incomplete_report_is_not_verified_zero(self):
        subject, html = report.build(self.cfg, self.data, [property_row(False)], [])
        self.assertIn("INCOMPLETE LIEN CHECKS", subject)
        self.assertIn("0 of 1 checks completed; 1 unchecked", html)
        self.assertIn("No verified ⭐ picks yet", html)
        self.assertNotIn("No properties meet every", html)

    def test_partial_coverage_retains_only_verified_picks(self):
        verified = property_row(True)
        verified["star"] = True
        subject, html = report.build(self.cfg, self.data, [verified, property_row(False)], [])
        self.assertIn("INCOMPLETE LIEN CHECKS", subject)
        self.assertIn("1 of 2 checks completed; 1 unchecked", html)
        self.assertIn("1 Picks", subject)
        self.assertNotIn("No properties meet every", html)

    def test_completed_zero_star_report(self):
        subject, html = report.build(self.cfg, self.data, [property_row()], [])
        self.assertNotIn("INCOMPLETE", subject)
        self.assertIn("1 of 1 checks completed", html)
        self.assertIn("No properties meet every", html)

    def test_empty_report_is_not_a_failure(self):
        subject, html = report.build(self.cfg, self.data, [], [])
        self.assertNotIn("INCOMPLETE", subject)
        self.assertIn("No properties are in range", html)

    def test_unchecked_property_cannot_star(self):
        self.assertFalse(main.is_star(property_row(False), self.cfg))
        self.assertTrue(main.is_star(property_row(True), self.cfg))

    def test_error_diagnostics_never_include_exception_text(self):
        error = RuntimeError("PRIVATE OWNER PRIVATE ADDRESS https://secret.example")
        error.stage = "grid_results"
        error.status_code = 503
        clerk = Mock()
        clerk.search_name.side_effect = error
        with patch.object(main.time, "sleep"):
            result = main.check_liens(clerk, "SMITH JANE", "LOT 1")
        self.assertFalse(result["checked"])
        self.assertIsNone(result["active_gov_liens"])
        self.assertIn("HTTP 503", result["notes"])
        self.assertIn("grid_results", result["notes"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("https://", json.dumps(result))
        error.stage = "PRIVATE ADDRESS"
        self.assertNotIn("PRIVATE", main._clerk_failure_reason(error))

    def test_unsearchable_owner_does_not_call_clerk(self):
        clerk = Mock()
        result = main.check_liens(clerk, "", "")
        self.assertFalse(result["checked"])
        self.assertIn("unsearchable", result["notes"])
        clerk.search_name.assert_not_called()

    def test_verified_empty_search_has_zero_liens(self):
        clerk = Mock()
        clerk.search_name.return_value = []
        with patch.object(main.time, "sleep"):
            result = main.check_liens(clerk, "SMITH JANE", "LOT 1")
        self.assertTrue(result["checked"])
        self.assertEqual(result["active_gov_liens"], 0)

    def run_fixture(self, owner="SMITH JANE", failure=False, send_email=False, empty=False):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            cfg = Config(home_address="PRIVATE HOME", state_file=Path(tmp) / "state.json",
                         output_html=Path(tmp) / "report.html")
            row = property_row()
            active = [] if empty else [row]
            patches = {
                "Config.from_env": cfg,
                "session": Mock(), "parcels.geocode": (1, 2),
                "auctions.upcoming": {"dates": [], "active": active},
                "parcels.gis_parcels": {"parcel-1": {"lon": 1, "lat": 2, "STRAP": "123",
                                                       "OWNER1": owner, "LEGAL": "LOT 1"}},
                "parcels.miles": 1, "parcels.pcpao_details": row,
                "flood.usgs_elevation_ft": 25, "flood.fema_zone": ("X", None),
                "flood.flood_factor": (1, "https://example.org"), "time.sleep": None,
            }
            # PCPAO must not overwrite the owner's GIS value in this fixture.
            patches["parcels.pcpao_details"] = {k: v for k, v in row.items() if k != "owner"}
            for target, value in patches.items():
                stack.enter_context(patch("taxlien.main." + target, return_value=value))
            clerk = stack.enter_context(patch("taxlien.main.liens.Clerk")).return_value
            clerk.search_name.return_value = []
            if failure:
                clerk.search_name.side_effect = RuntimeError("PRIVATE OWNER PRIVATE HOME")
            send = stack.enter_context(patch("taxlien.main.emailer.send"))
            outputs = Path(tmp) / "github-output"
            stack.enter_context(patch.dict(os.environ, {"GITHUB_OUTPUT": str(outputs)}))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = main.run(send_email=send_email)
            self.assertEqual(outputs.read_text(), "report_generated=true\n")
            self.assertTrue(cfg.output_html.exists())
            self.assertTrue(cfg.state_file.exists())
            if send_email:
                send.assert_called_once()
            else:
                send.assert_not_called()
            self.assertNotIn("PRIVATE", stdout.getvalue())
            return status, cfg.output_html.read_text(), json.loads(cfg.state_file.read_text())

    def test_failed_run_writes_report_without_email_and_returns_one(self):
        status, html, state = self.run_fixture(failure=True)
        self.assertEqual(status, 1)
        self.assertIn("Incomplete lien coverage", html)
        self.assertFalse(state["properties"][0]["liens"]["checked"])

    def test_failed_run_preserves_existing_email_path(self):
        status, _, _ = self.run_fixture(failure=True, send_email=True)
        self.assertEqual(status, 1)

    def test_unsearchable_run_returns_one(self):
        status, _, _ = self.run_fixture(owner="")
        self.assertEqual(status, 1)

    def test_completed_run_returns_zero(self):
        status, _, _ = self.run_fixture()
        self.assertEqual(status, 0)

    def test_empty_run_returns_zero(self):
        status, _, _ = self.run_fixture(empty=True)
        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
