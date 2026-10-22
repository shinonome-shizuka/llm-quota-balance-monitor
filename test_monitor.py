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


class TypeSafeBalanceTests(unittest.TestCase):
    def setUp(self):
        monitor._typesafe_action_cache.update({"id": None, "expires_at": 0.0})

    def test_parses_billing_balance_and_credits(self):
        body = '\n'.join([
            '0:{"a":"$@1"}',
            '1:{"ok":true,"data":{"billing":{"plan":"free_plan","spent":0.01,'
            '"balance":4.98,"cycleLabel":"September 2026","credits":[{"amount":5,'
            '"remaining":4.98,"expiresAt":"2026-10-19T00:00:00Z"}]}}}',
        ])

        data, error = monitor._parse_typesafe_billing(body)

        self.assertIsNone(error)
        self.assertEqual(data["balance"], 4.98)
        self.assertEqual(data["spent"], 0.01)
        self.assertEqual(data["plan"], "Free")
        self.assertEqual(data["cycle"], "September 2026")
        self.assertEqual(data["credits"][0]["remaining"], 4.98)

    def test_zero_balance_is_valid(self):
        body = '1:{"ok":true,"data":{"billing":{"spent":5,"balance":0}}}'

        data, error = monitor._parse_typesafe_billing(body)

        self.assertIsNone(error)
        self.assertEqual(data["balance"], 0.0)

    def test_missing_numeric_balance_is_rejected(self):
        body = '1:{"ok":true,"data":{"billing":{"spent":1,"balance":null}}}'

        data, error = monitor._parse_typesafe_billing(body)

        self.assertIsNone(data)
        self.assertEqual(error, "TypeSafe balance missing")

    def test_empty_cookie_does_not_make_network_request(self):
        with patch("monitor._typesafe_cookie_from_keychain", return_value=""):
            data, error = monitor.fetch_typesafe("")

        self.assertIsNone(data)
        self.assertEqual(error, "No console Cookie header")

    @patch("monitor._typesafe_cookie_from_keychain", return_value="session=from-keychain")
    @patch("monitor._typesafe_request")
    @patch("monitor._discover_typesafe_action", return_value=("c" * 40, None))
    @patch("monitor.time.monotonic", return_value=1000)
    def test_uses_keychain_cookie_when_config_is_empty(
        self, monotonic, discover, request, keychain
    ):
        request.return_value = (
            200,
            {},
            '1:{"ok":true,"data":{"billing":{"spent":0,"balance":3}}}',
        )

        data, error = monitor.fetch_typesafe("")

        self.assertIsNone(error)
        self.assertEqual(data["balance"], 3)
        discover.assert_called_once_with("session=from-keychain")

    @patch("monitor._typesafe_request")
    @patch("monitor._discover_typesafe_action", return_value=("b" * 40, None))
    @patch("monitor.time.monotonic", return_value=1000)
    def test_accepts_cookie_header_prefix(self, monotonic, discover, request):
        request.return_value = (
            200,
            {},
            '1:{"ok":true,"data":{"billing":{"spent":0,"balance":5}}}',
        )

        data, error = monitor.fetch_typesafe("Cookie: first=one; second=two")

        self.assertIsNone(error)
        self.assertEqual(data["balance"], 5)
        discover.assert_called_once_with("first=one; second=two")

    @patch("monitor._typesafe_request")
    @patch("monitor._discover_typesafe_action", return_value=("a" * 40, None))
    @patch("monitor.time.monotonic", return_value=1000)
    def test_fetch_posts_discovered_server_action(self, monotonic, discover, request):
        request.return_value = (
            200,
            {"Content-Type": "text/x-component"},
            '1:{"ok":true,"data":{"billing":{"spent":1.25,"balance":8.75}}}',
        )

        data, error = monitor.fetch_typesafe("session=value")

        self.assertIsNone(error)
        self.assertEqual(data["balance"], 8.75)
        discover.assert_called_once_with("session=value")
        _, kwargs = request.call_args
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["headers"]["Next-Action"], "a" * 40)
        self.assertEqual(kwargs["data"], b"[]")


if __name__ == "__main__":
    unittest.main()
