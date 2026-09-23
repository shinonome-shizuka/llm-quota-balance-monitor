import unittest
from unittest.mock import patch

import monitor


class KimiSubscriptionUsageTests(unittest.TestCase):
    def test_returns_whole_subscription_usage(self):
        data = {
            "subscription_usage": {"used": 37, "limit": 100, "percent": 37},
            "rows": [{"label": "Weekly Usage", "used": 46, "limit": 100}],
        }

        self.assertEqual(monitor._kimi_subscription_usage(data)["percent"], 37)

    def test_does_not_fall_back_to_weekly_usage(self):
        data = {
            "subscription_usage": None,
            "rows": [{"label": "Weekly Usage", "used": 46, "limit": 100}],
        }

        self.assertIsNone(monitor._kimi_subscription_usage(data))

    def test_returns_none_without_data(self):
        self.assertIsNone(monitor._kimi_subscription_usage({}))
        self.assertIsNone(monitor._kimi_subscription_usage(None))

    def test_explicitly_empty_total_quota_is_treated_as_exhausted(self):
        self.assertTrue(monitor._kimi_total_quota_exhausted({
            "subscription_usage": None,
            "total_quota_empty": True,
            "total_quota_exhausted": True,
            "rows": [{"used": 46, "limit": 100, "percent": 46}],
        }))

    def test_missing_total_quota_is_not_treated_as_exhausted(self):
        self.assertFalse(monitor._kimi_total_quota_exhausted({
            "subscription_usage": None,
            "total_quota_empty": False,
            "total_quota_exhausted": False,
        }))

    def test_explicitly_depleted_total_quota_is_exhausted(self):
        self.assertTrue(monitor._kimi_total_quota_exhausted({
            "subscription_usage": {"used": 100, "limit": 100, "percent": 100},
            "rows": [{"used": 46, "limit": 100, "percent": 46}],
        }))

    def test_non_depleted_total_quota_is_not_exhausted(self):
        self.assertFalse(monitor._kimi_total_quota_exhausted({
            "subscription_usage": {"used": 99, "limit": 100, "percent": 99},
        }))

    @patch.dict("os.environ", {monitor.KIMI_OUT_TEST_ENV: "1"})
    def test_explicit_test_override_simulates_exhausted_total_quota(self):
        payload = {"usage": {"limit": "100", "remaining": "54"}, "totalQuota": {}}

        overridden = monitor._apply_kimi_test_override(payload)

        self.assertEqual(overridden["totalQuota"]["remaining"], "0")
        self.assertEqual(overridden["totalQuota"]["used"], "100")
        self.assertEqual(payload["totalQuota"], {})

    @patch.dict("os.environ", {}, clear=True)
    def test_test_override_is_disabled_by_default(self):
        payload = {"totalQuota": {}}

        self.assertIs(monitor._apply_kimi_test_override(payload), payload)

    @patch("monitor._run_kimi_total_quota_probe", return_value=False)
    @patch("monitor.time.monotonic", side_effect=[1000, 1001])
    def test_consuming_probe_is_cached_for_24_hours(self, monotonic, run_probe):
        monitor._kimi_probe_cache.clear()

        first = monitor._probe_kimi_total_quota_exhausted("cache-test-key")
        second = monitor._probe_kimi_total_quota_exhausted("cache-test-key")

        self.assertFalse(first)
        self.assertFalse(second)
        run_probe.assert_called_once_with("cache-test-key")

    @patch("monitor._run_kimi_total_quota_probe", side_effect=[False, True])
    @patch("monitor.time.monotonic", side_effect=[1000, 1000 + 24 * 60 * 60])
    def test_consuming_probe_repeats_after_24_hours(self, monotonic, run_probe):
        monitor._kimi_probe_cache.clear()

        self.assertFalse(monitor._probe_kimi_total_quota_exhausted("expiry-test-key"))
        self.assertTrue(monitor._probe_kimi_total_quota_exhausted("expiry-test-key"))
        self.assertEqual(run_probe.call_count, 2)

    @patch("monitor.fetch_json")
    def test_fetch_parses_total_quota_separately_from_weekly_usage(self, fetch_json):
        fetch_json.return_value = {
            "usage": {"limit": "100", "used": "46", "remaining": "54"},
            "limits": [],
            "totalQuota": {"limit": "100", "remaining": "63"},
        }

        data, error = monitor.fetch_kimi("test-key")

        self.assertIsNone(error)
        self.assertEqual(data["rows"][0]["percent"], 46)
        self.assertEqual(data["subscription_usage"]["used"], 37)
        self.assertEqual(data["subscription_usage"]["percent"], 37)
        self.assertFalse(data["total_quota_empty"])

    @patch("monitor._probe_kimi_total_quota_exhausted", return_value=True)
    @patch.dict("os.environ", {}, clear=True)
    @patch("monitor.fetch_json")
    def test_fetch_probes_empty_total_quota(self, fetch_json, probe):
        fetch_json.return_value = {
            "usage": {"limit": "100", "used": "46", "remaining": "54"},
            "limits": [],
            "totalQuota": {},
        }

        data, error = monitor.fetch_kimi("exhausted-test-key")

        self.assertIsNone(error)
        self.assertEqual(data["rows"][0]["percent"], 46)
        self.assertTrue(data["total_quota_empty"])
        self.assertTrue(data["total_quota_exhausted"])
        self.assertTrue(monitor._kimi_total_quota_exhausted(data))
        probe.assert_called_once_with("exhausted-test-key")

    @patch("monitor._probe_kimi_total_quota_exhausted", return_value=False)
    @patch.dict("os.environ", {}, clear=True)
    @patch("monitor.fetch_json")
    def test_empty_total_quota_with_successful_probe_keeps_weekly(self, fetch_json, probe):
        fetch_json.return_value = {
            "usage": {"limit": "100", "used": "46", "remaining": "54"},
            "limits": [],
            "totalQuota": {},
        }

        data, error = monitor.fetch_kimi("active-test-key")

        self.assertIsNone(error)
        self.assertFalse(data["total_quota_exhausted"])
        self.assertFalse(monitor._kimi_total_quota_exhausted(data))
        probe.assert_called_once_with("active-test-key")


class CodexUsageTests(unittest.TestCase):
    def test_reads_codex_account_email_as_name(self):
        payload = {
            "account": {"type": "chatgpt", "email": "user@example.com", "planType": "pro"},
            "requiresOpenaiAuth": True,
        }

        self.assertEqual(monitor._codex_account_name(payload), "user@example.com")

    def test_missing_codex_account_name_is_allowed(self):
        self.assertIsNone(monitor._codex_account_name({"account": None}))

    def test_parses_codex_subscription_window(self):
        payload = {
            "ordinaryUsageAllowed": True,
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {
                        "usedPercent": 85,
                        "windowDurationMins": 10080,
                        "resetsAt": 1789956797,
                    },
                    "secondary": None,
                    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                    "planType": "pro",
                    "rateLimitReachedType": None,
                    "spendControlReached": False,
                }
            },
            "rateLimits": {},
        }

        data = monitor._parse_codex_rate_limits(payload)

        self.assertEqual(data["remaining_pct"], 15)
        self.assertEqual(data["used_pct"], 85)
        self.assertEqual(data["plan"], "pro")
        self.assertFalse(data["exhausted"])
        self.assertEqual(data["credits_balance"], "0")

    def test_codex_out_when_ordinary_usage_is_blocked(self):
        payload = {
            "ordinaryUsageAllowed": False,
            "rateLimits": {
                "primary": {"usedPercent": 85},
                "rateLimitReachedType": "rate_limit_reached",
            },
        }

        data = monitor._parse_codex_rate_limits(payload)

        self.assertTrue(data["exhausted"])

    def test_codex_percent_is_clamped(self):
        payload = {"rateLimits": {"primary": {"usedPercent": 120}}}

        data = monitor._parse_codex_rate_limits(payload)

        self.assertEqual(data["used_pct"], 100)
        self.assertEqual(data["remaining_pct"], 0)
        self.assertTrue(data["exhausted"])


if __name__ == "__main__":
    unittest.main()
