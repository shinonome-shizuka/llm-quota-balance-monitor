#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["rumps"]
# ///
"""LLM Quota Balance Monitor — macOS menu bar app."""

import json
import os
import queue
import re
import select
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

try:
    import rumps
except ImportError:
    rumps = None  # only needed for the menu bar app, not for --status

CONFIG_DIR = Path(__file__).resolve().parent
CONFIG_FILE = CONFIG_DIR / "config.json"
REFRESH_INTERVAL = 30 * 60  # 30 minutes
KIMI_OUT_TEST_ENV = "KIMI_SIMULATE_TOTAL_QUOTA_OUT"
KIMI_OUT_PROBE_INTERVAL = 24 * 60 * 60
_kimi_probe_cache = {}
_kimi_probe_lock = threading.Lock()
_typesafe_action_cache = {"id": None, "expires_at": 0.0}
_typesafe_action_lock = threading.Lock()


def ensure_config():
    """Create config file if missing. Returns True if created."""
    if CONFIG_FILE.exists():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({
        "enabled": ["deepseek", "minimax", "aikeybest", "niumacode", "kimi", "glm", "codex", "typesafe"],
        "deepseek_api_key": "",
        "minimax_subscription_key": "",
        "aikeybest_token": "",
        "niumacode_api_key": "",
        "kimi_api_key": "",
        "glm_api_key": "",
        "typesafe_cookie": ""
    }, indent=2))
    return True


def load_config():
    cfg = json.loads(CONFIG_FILE.read_text())
    if "enabled" not in cfg:
        cfg["enabled"] = ["deepseek", "minimax", "aikeybest", "niumacode", "kimi", "glm", "codex", "typesafe"]
    return cfg


def is_enabled(cfg, name):
    return name in cfg.get("enabled", [])


def format_number(num):
    """Format large numbers with K/M/B suffixes."""
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    else:
        return str(num)


def fetch_json(url, api_key, extra_headers=None):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "curl/8.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _kimi_reset_info(data):
    from datetime import datetime, timedelta
    reset_at = data.get("resetTime") or data.get("reset_at") or data.get("reset_time")
    if reset_at:
        try:
            if isinstance(reset_at, (int, float)):
                dt = datetime.fromtimestamp(reset_at)
            else:
                dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00")).astimezone()
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            diff = dt - now
            if diff.total_seconds() <= 0:
                return dt.strftime("%m-%d %H:%M"), "0m"
            days = diff.days
            hours, rem = divmod(diff.seconds, 3600)
            minutes, _ = divmod(rem, 60)
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0:
                parts.append(f"{hours}h")
            parts.append(f"{minutes}m")
            return dt.strftime("%m-%d %H:%M"), " ".join(parts)
        except Exception:
            pass
    reset_in = _to_int(data.get("reset_in"))
    if reset_in is not None:
        dt = datetime.now() + timedelta(seconds=reset_in)
        hours, rem = divmod(reset_in, 3600)
        minutes, _ = divmod(rem, 60)
        return dt.strftime("%m-%d %H:%M"), f"{hours}h {minutes}m"
    return None


def _kimi_limit_label(window, idx):
    duration = _to_int(window.get("duration") or window.get("time_window", {}).get("duration"))
    time_unit = str(window.get("timeUnit") or window.get("time_unit") or window.get("time_window", {}).get("time_unit") or "").upper()
    if duration is not None:
        if "MINUTE" in time_unit:
            if duration >= 60 and duration % 60 == 0:
                return f"{duration // 60}h Limit"
            return f"{duration}m Limit"
        if "HOUR" in time_unit:
            return f"{duration}h Limit"
        if "DAY" in time_unit:
            return f"{duration}d Limit"
        if "MONTH" in time_unit:
            return f"{duration}mo Limit"
        return f"{duration}s Limit"
    return f"Limit #{idx + 1}"


def _kimi_usage_row(data, default_label):
    limit = _to_int(data.get("limit") or data.get("limit_amount") or data.get("total"))
    used = _to_int(data.get("used") or data.get("used_amount") or data.get("current"))
    if used is None:
        remaining = _to_int(data.get("remaining"))
        if remaining is not None and limit is not None:
            used = limit - remaining
    if used is None and limit is None:
        return None
    reset_info = _kimi_reset_info(data)
    reset_at, countdown = reset_info if reset_info else (None, None)
    return {
        "label": str(data.get("name") or data.get("title") or data.get("model_name") or default_label),
        "used": used or 0,
        "limit": limit or 0,
        "reset_at": reset_at,
        "countdown": countdown,
    }


def _parse_kimi_usage_payload(payload):
    summary = None
    limits = []
    data_list = payload.get("data")
    if isinstance(data_list, list):
        for item in data_list:
            if not isinstance(item, dict):
                continue
            label = "Weekly Usage" if item.get("model_name") == "all" else "Limit"
            row = _kimi_usage_row(item, default_label=label)
            if row:
                if item.get("model_name") == "all":
                    summary = row
                else:
                    limits.append(row)
    else:
        usage = payload.get("usage")
        if isinstance(usage, dict):
            summary = _kimi_usage_row(usage, default_label="Weekly Usage")
        raw_limits = payload.get("limits")
        if isinstance(raw_limits, list):
            for idx, item in enumerate(raw_limits):
                if not isinstance(item, dict):
                    continue
                detail = item.get("detail") if isinstance(item.get("detail"), dict) else item
                window = item.get("window") if isinstance(item.get("window"), dict) else item.get("time_window") if isinstance(item.get("time_window"), dict) else {}
                row = _kimi_usage_row(detail, default_label=_kimi_limit_label(window, idx))
                if row:
                    limits.append(row)
    return summary, limits


def _kimi_subscription_usage(data):
    """Return Kimi's whole-subscription-period quota, not its weekly window."""
    if not data:
        return None
    return data.get("subscription_usage")


def _kimi_total_quota_exhausted(data):
    """Only report exhaustion when Kimi explicitly returns a depleted total quota."""
    if data and data.get("total_quota_exhausted") is True:
        return True
    subscription = _kimi_subscription_usage(data)
    if not subscription:
        return False
    limit = _to_int(subscription.get("limit"))
    used = _to_int(subscription.get("used"))
    return limit is not None and limit > 0 and used is not None and used >= limit


def _apply_kimi_test_override(payload):
    """Inject an exhausted total quota only for an explicitly enabled UI test."""
    if os.environ.get(KIMI_OUT_TEST_ENV) != "1":
        return payload
    overridden = dict(payload)
    overridden["totalQuota"] = {"limit": "100", "used": "100", "remaining": "0"}
    return overridden


def _run_kimi_total_quota_probe(key):
    """Probe an empty totalQuota with a one-token completion.

    Returns True only for Kimi's explicit billing-cycle exhaustion error,
    False when the request succeeds, and None for unrelated failures.
    """
    body = json.dumps({
        "model": "kimi-for-coding",
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 1,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        "https://api.kimi.com/coding/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "KimiCLI/1.6",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Consume the response so the connection can be cleanly reused.
            resp.read()
            return False
    except urllib.error.HTTPError as e:
        error_text = e.read().decode(errors="replace").lower()
        if e.code == 403 and "usage limit for this billing cycle" in error_text:
            return True
        return None
    except urllib.error.URLError:
        return None


def _probe_kimi_total_quota_exhausted(key):
    """Run the consuming probe at most once per 24 hours for each API key."""
    now = time.monotonic()
    with _kimi_probe_lock:
        cached = _kimi_probe_cache.get(key)
        if cached and now - cached[0] < KIMI_OUT_PROBE_INTERVAL:
            return cached[1]

        result = _run_kimi_total_quota_probe(key)
        _kimi_probe_cache[key] = (now, result)
        return result


def fetch_deepseek(key):
    if not key:
        return None, "No API key"
    data = fetch_json("https://api.deepseek.com/user/balance", key)
    info = data["balance_infos"][0] if data.get("balance_infos") else None
    if not info:
        return None, "No balance info"
    return {
        "available": data.get("is_available", False),
        "total": info.get("total_balance", "0"),
        "granted": info.get("granted_balance", "0"),
        "topped_up": info.get("topped_up_balance", "0"),
        "currency": info.get("currency", "CNY"),
    }, None


def fetch_minimax(key):
    if not key:
        return None, "No API key"
    data = fetch_json("https://api.minimaxi.com/v1/token_plan/remains", key)
    base = data.get("base_resp", {})
    if base.get("status_code", -1) != 0:
        return None, base.get("status_msg", "API error")
    remains = data.get("model_remains", [])
    if not remains:
        return None, "No model_remains"

    models = []
    general_weekly_pct = 0
    for r in remains:
        model_name = r.get("model_name", "unknown")
        interval_pct = r.get("current_interval_remaining_percent", 0)
        weekly_pct = r.get("current_weekly_remaining_percent", 0)
        end_time = r.get("end_time", "")
        reset_str = ""
        if end_time:
            try:
                # end_time is milliseconds timestamp
                dt = datetime.fromtimestamp(end_time / 1000)
                reset_str = dt.strftime("%H:%M")
            except Exception:
                pass
        models.append({
            "name": model_name,
            "interval_pct": interval_pct,
            "weekly_pct": weekly_pct,
            "reset_at": reset_str,
        })
        if model_name == "general":
            general_weekly_pct = weekly_pct

    return {
        "models": models,
        "avg_pct": general_weekly_pct,
    }, None


def fetch_niumacode(key):
    if not key:
        return None, "No API key"
    data = fetch_json(
        "https://www.niumacode.cc/v1/usage?days=30&timezone=Asia/Shanghai", key)
    if not data.get("isValid", True):
        return None, "Invalid key"
    balance = data.get("balance") or data.get("remaining", 0)
    return {
        "balance": balance,
        "unit": data.get("unit", "USD"),
        "plan": data.get("planName", ""),
        "total_cost": data.get("usage", {}).get("total", {}).get("cost", 0),
    }, None


def fetch_kimi(key):
    if not key:
        return None, "No API key"
    base_url = "https://api.kimi.com/coding/v1"
    url = base_url + "/usages"
    try:
        payload = fetch_json(url, key, extra_headers={"User-Agent": "KimiCLI/1.6"})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            fallback_url = base_url + "/usage"
            try:
                payload = fetch_json(fallback_url, key, extra_headers={"User-Agent": "KimiCLI/1.6"})
            except urllib.error.HTTPError as fe:
                text = fe.read().decode()[:500]
                return None, f"Kimi Code usage API error {fe.code}: {text}"
        else:
            text = e.read().decode()[:500]
            return None, f"Kimi Code usage API error {e.code}: {text}"
    except urllib.error.URLError as e:
        return None, f"Kimi Code API unreachable: {e.reason}"

    payload = _apply_kimi_test_override(payload)
    summary, limits = _parse_kimi_usage_payload(payload)
    subscription_usage = None
    has_total_quota = "totalQuota" in payload or "total_quota" in payload
    raw_total_quota = (
        payload.get("totalQuota")
        if "totalQuota" in payload
        else payload.get("total_quota")
    )
    total_quota_empty = has_total_quota and raw_total_quota == {}
    total_quota_exhausted = False
    if total_quota_empty:
        total_quota_exhausted = _probe_kimi_total_quota_exhausted(key) is True
    if isinstance(raw_total_quota, dict) and raw_total_quota:
        subscription_usage = _kimi_usage_row(
            raw_total_quota, default_label="Subscription Usage")
        if subscription_usage:
            limit = subscription_usage["limit"]
            used = subscription_usage["used"]
            subscription_usage["percent"] = int(used / limit * 100) if limit > 0 else 0
    rows = []
    if summary:
        rows.append({
            "label": summary["label"] or "Weekly Usage",
            "used": summary["used"],
            "limit": summary["limit"],
            "percent": int(summary["used"] / summary["limit"] * 100) if summary["limit"] > 0 else 0,
            "reset_at": summary["reset_at"],
            "countdown": summary["countdown"],
        })
    for i, lim in enumerate(limits):
        rows.append({
            "label": lim["label"] or f"Limit #{i + 1}",
            "used": lim["used"],
            "limit": lim["limit"],
            "percent": int(lim["used"] / lim["limit"] * 100) if lim["limit"] > 0 else 0,
            "reset_at": lim["reset_at"],
            "countdown": lim["countdown"],
        })
    if not rows:
        return None, "No usage data returned"
    return {
        "rows": rows,
        "subscription_usage": subscription_usage,
        "total_quota_empty": total_quota_empty,
        "total_quota_exhausted": total_quota_exhausted,
    }, None


def fetch_glm(key):
    if not key:
        return None, "No API key"
    quota = fetch_json(
        "https://open.bigmodel.cn/api/monitor/usage/quota/limit", key)
    if not quota.get("success", False):
        return None, quota.get("msg", "API error")
    limits = quota.get("data", {}).get("limits", []) or []
    total_used = 0
    total_limit = 0
    for lim in limits:
        used = float(lim.get("used", 0) or 0)
        limit = float(lim.get("limit", 0) or 0)
        total_used += used
        total_limit += limit
    pct = int((1 - total_used / total_limit) * 100) if total_limit > 0 else 0
    return {
        "pct": pct,
        "used": total_used,
        "limit": total_limit,
        "plans": [l.get("type", "") for l in limits],
    }, None


def fetch_aikeybest(token):
    if not token:
        return None, "No token"
    data = fetch_json("https://api.aikeybest.cn/api/usage/token/", token)
    if not data.get("code"):
        return None, data.get("message", "API error")
    d = data.get("data", {})
    return {
        "name": d.get("name", ""),
        "total": d.get("total_granted", 0),
        "used": d.get("total_used", 0),
        "available": d.get("total_available", 0),
        "unlimited": d.get("unlimited_quota", False),
    }, None


def _find_codex_executable():
    """Locate the installed Codex CLI without requiring configuration."""
    candidates = [
        shutil.which("codex"),
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _codex_window(window):
    if not isinstance(window, dict):
        return None
    used = _to_int(window.get("usedPercent"))
    if used is None:
        return None
    used = max(0, min(100, used))
    duration = _to_int(window.get("windowDurationMins"))
    resets_at = _to_int(window.get("resetsAt"))
    return {
        "used_pct": used,
        "remaining_pct": 100 - used,
        "duration_mins": duration,
        "reset_at": datetime.fromtimestamp(resets_at).strftime("%m-%d %H:%M") if resets_at else None,
    }


def _parse_codex_rate_limits(payload):
    limits_by_id = payload.get("rateLimitsByLimitId")
    snapshot = limits_by_id.get("codex") if isinstance(limits_by_id, dict) else None
    if not isinstance(snapshot, dict):
        snapshot = payload.get("rateLimits")
    if not isinstance(snapshot, dict):
        return None

    primary = _codex_window(snapshot.get("primary"))
    secondary = _codex_window(snapshot.get("secondary"))
    if primary is None and secondary is None:
        return None
    main = primary or secondary
    exhausted = (
        payload.get("ordinaryUsageAllowed") is False
        or snapshot.get("rateLimitReachedType") is not None
        or snapshot.get("spendControlReached") is True
        or main["remaining_pct"] <= 0
    )
    credits = snapshot.get("credits") if isinstance(snapshot.get("credits"), dict) else {}
    return {
        "remaining_pct": main["remaining_pct"],
        "used_pct": main["used_pct"],
        "exhausted": exhausted,
        "plan": snapshot.get("planType") or "unknown",
        "primary": primary,
        "secondary": secondary,
        "credits_balance": credits.get("balance"),
        "credits_unlimited": credits.get("unlimited", False),
    }


def _codex_account_name(payload):
    account = payload.get("account") if isinstance(payload, dict) else None
    if not isinstance(account, dict):
        return None
    return account.get("email") or None


def fetch_codex():
    """Read the signed-in ChatGPT/Codex subscription limits via app-server."""
    executable = _find_codex_executable()
    if not executable:
        return None, "Codex CLI not found"

    process = None
    try:
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        requests = [
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "llm-quota-balance-monitor", "version": "1.0"},
                    "capabilities": {},
                },
            },
            {"method": "initialized"},
            {"id": 3, "method": "account/read", "params": {"refreshToken": False}},
            {
                "id": 2,
                "method": "account/rateLimits/read",
                "params": {"excludeResetCreditDetails": True},
            },
        ]
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()

        deadline = time.monotonic() + 15
        parsed = None
        account_name = None
        account_received = False
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 1)
            if not ready:
                if process.poll() is not None:
                    return None, "Codex app-server exited"
                continue
            line = process.stdout.readline()
            if not line:
                return None, "Codex app-server closed output"
            response = json.loads(line)
            response_id = response.get("id")
            if response_id == 3:
                account_received = True
                if not response.get("error"):
                    account_name = _codex_account_name(response.get("result") or {})
                if parsed is not None:
                    parsed["account_name"] = account_name
                    return parsed, None
                continue
            if response_id != 2:
                continue
            if response.get("error"):
                error = response["error"]
                return None, str(error.get("message") if isinstance(error, dict) else error)
            parsed = _parse_codex_rate_limits(response.get("result") or {})
            if parsed is None:
                return None, "No Codex rate limits returned"
            if account_received:
                parsed["account_name"] = account_name
                return parsed, None
        if parsed is not None:
            parsed["account_name"] = account_name
            return parsed, None
        return None, "Codex usage request timed out"
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return None, f"Codex usage error: {e}"
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


TYPESAFE_BILLING_URL = "https://console.typesafe.ai/settings/billing"
TYPESAFE_ORIGIN = "https://console.typesafe.ai"
TYPESAFE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
TYPESAFE_KEYCHAIN_SERVICE = "llm-quota-balance-monitor.typesafe-cookie"
TYPESAFE_KEYCHAIN_ACCOUNT = "typesafe"


def _typesafe_cookie_from_keychain():
    if sys.platform != "darwin" or shutil.which("security") is None:
        return ""
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password", "-w",
                "-s", TYPESAFE_KEYCHAIN_SERVICE,
                "-a", TYPESAFE_KEYCHAIN_ACCOUNT,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _typesafe_request(url, cookie, method="GET", headers=None, data=None, timeout=10):
    request_headers = {
        "Cookie": cookie,
        "User-Agent": TYPESAFE_USER_AGENT,
        "Accept": "text/html",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method)
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read().decode(errors="replace")


def _typesafe_validate_response(status, body):
    if status in (401, 403) or 300 <= status < 400:
        return "TypeSafe session expired"
    if status == 429:
        return "TypeSafe rate limit reached"
    if status == 408 or status >= 500:
        return "TypeSafe billing unavailable"
    if status < 200 or status >= 300:
        return f"TypeSafe billing HTTP {status}"
    # Next.js can return the login route with HTTP 200.
    if re.search(r'\\?"\(auth\)\\?".*?\\?"login\\?"', body):
        return "TypeSafe session expired"
    return None


def _discover_typesafe_action(cookie):
    status, _, page = _typesafe_request(TYPESAFE_BILLING_URL, cookie)
    error = _typesafe_validate_response(status, page)
    if error:
        return None, error
    script_sources = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>', page, re.IGNORECASE)
    seen = set()
    for source in script_sources[:60]:
        url = urllib.parse.urljoin(TYPESAFE_ORIGIN, source)
        if not url.startswith(TYPESAFE_ORIGIN + "/") or not re.search(r"\.js(?:$|\?)", url, re.I):
            continue
        if url in seen:
            continue
        seen.add(url)
        chunk_status, _, chunk = _typesafe_request(url, cookie, timeout=4)
        if chunk_status == 429 or chunk_status == 408 or chunk_status >= 500:
            return None, _typesafe_validate_response(chunk_status, chunk)
        if not 200 <= chunk_status < 300:
            continue
        match = re.search(
            r'"([0-9a-f]{40,})"[^)]{0,150}"getBillingOverviewResult"',
            chunk,
            re.IGNORECASE,
        )
        if match:
            return match.group(1), None
    return None, "TypeSafe billing action not found"


def _parse_typesafe_billing(body):
    result = None
    for line in body.splitlines():
        separator = line.find(":")
        if separator < 1:
            continue
        try:
            candidate = json.loads(line[separator + 1:])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(candidate, dict) and "ok" in candidate:
            result = candidate
            break
    if not result or result.get("ok") is not True:
        return None, "TypeSafe billing response invalid"
    billing = (result.get("data") or {}).get("billing")
    if not isinstance(billing, dict):
        return None, "TypeSafe billing data missing"
    balance = billing.get("balance")
    spent = billing.get("spent")
    if not isinstance(balance, (int, float)) or isinstance(balance, bool):
        return None, "TypeSafe balance missing"
    if not isinstance(spent, (int, float)) or isinstance(spent, bool):
        return None, "TypeSafe spend missing"
    plan = billing.get("plan") if isinstance(billing.get("plan"), str) else None
    if plan:
        plan = " ".join(part.capitalize() for part in re.split(r"[_-]+", plan) if part)
        if plan == "Free Plan":
            plan = "Free"
    credits = []
    for item in billing.get("credits") or []:
        if not isinstance(item, dict):
            continue
        amount = item.get("amount")
        remaining = item.get("remaining")
        expires_at = item.get("expiresAt")
        if (
            isinstance(amount, (int, float))
            and isinstance(remaining, (int, float))
            and not isinstance(amount, bool)
            and not isinstance(remaining, bool)
            and remaining > 0
            and isinstance(expires_at, str)
        ):
            credits.append({"amount": amount, "remaining": remaining, "expires_at": expires_at})
    return {
        "balance": float(balance),
        "spent": float(spent),
        "currency": "USD",
        "cycle": billing.get("cycleLabel") if isinstance(billing.get("cycleLabel"), str) else None,
        "plan": plan,
        "credits": credits,
    }, None


def fetch_typesafe(cookie):
    """Read TypeSafe balance from the signed-in console billing session."""
    cookie = (cookie or _typesafe_cookie_from_keychain()).strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if not cookie:
        return None, "No console Cookie header"

    with _typesafe_action_lock:
        now = time.monotonic()
        action_id = _typesafe_action_cache["id"]
        if not action_id or now >= _typesafe_action_cache["expires_at"]:
            action_id, error = _discover_typesafe_action(cookie)
            if error:
                return None, error
            _typesafe_action_cache.update({"id": action_id, "expires_at": now + 12 * 60 * 60})

    def post(current_action):
        return _typesafe_request(
            TYPESAFE_BILLING_URL,
            cookie,
            method="POST",
            headers={
                "Origin": TYPESAFE_ORIGIN,
                "Next-Action": current_action,
                "Accept": "text/x-component",
                "Content-Type": "application/json",
            },
            data=b"[]",
        )

    status, headers, body = post(action_id)
    lower_headers = {key.lower(): value for key, value in headers.items()}
    if status == 404 and lower_headers.get("x-nextjs-action-not-found") == "1":
        with _typesafe_action_lock:
            action_id, error = _discover_typesafe_action(cookie)
            if error:
                return None, error
            _typesafe_action_cache.update({"id": action_id, "expires_at": time.monotonic() + 12 * 60 * 60})
        status, _, body = post(action_id)
    error = _typesafe_validate_response(status, body)
    if error:
        return None, error
    return _parse_typesafe_billing(body)


if rumps is not None:
    class LLMMonitorApp(rumps.App):
        def __init__(self):
            super().__init__("LLM Monitor", title="Loading...")
            self.config = load_config()
            self.ds_data = None
            self.ds_error = None
            self.mm_data = None
            self.mm_error = None
            self.ak_data = None
            self.ak_error = None
            self.nm_data = None
            self.nm_error = None
            self.km_data = None
            self.km_error = None
            self.glm_data = None
            self.glm_error = None
            self.cx_data = None
            self.cx_error = None
            self.ts_data = None
            self.ts_error = None
            self.result_queue = queue.Queue()
            self.poll_timer = rumps.Timer(self.on_poll, 0.5)
            self.refresh_timer = rumps.Timer(self.on_tick, REFRESH_INTERVAL)
            self.poll_timer.start()
            self.refresh_timer.start()
            self._start_fetch()

        def refresh(self, _=None):
            self.title = "..."
            self._start_fetch()

        def on_poll(self, _=None):
            # Poll queue on main thread to update UI
            try:
                while True:
                    ds, mm, ak, nm, km, glm, cx, ts = self.result_queue.get_nowait()
                    self.ds_data, self.ds_error = ds
                    self.mm_data, self.mm_error = mm
                    self.ak_data, self.ak_error = ak
                    self.nm_data, self.nm_error = nm
                    self.km_data, self.km_error = km
                    self.glm_data, self.glm_error = glm
                    self.cx_data, self.cx_error = cx
                    self.ts_data, self.ts_error = ts
                    self.update_title()
                    self._build_menu()
            except queue.Empty:
                pass

        def on_tick(self, _=None):
            self._start_fetch()

        def _start_fetch(self):
            threading.Thread(target=self._fetch_and_update, daemon=True).start()

        def _fetch_and_update(self):
            self.config = load_config()
            cfg = self.config
            disabled = lambda n: (None, "Disabled")
            ds = mm = ak = nm = km = glm = cx = ts = (None, None)
            try:
                ds = fetch_deepseek(cfg["deepseek_api_key"]) if is_enabled(cfg, "deepseek") else disabled("deepseek")
            except Exception as e:
                ds = None, str(e)
            try:
                mm = fetch_minimax(cfg["minimax_subscription_key"]) if is_enabled(cfg, "minimax") else disabled("minimax")
            except Exception as e:
                mm = None, str(e)
            try:
                ak = fetch_aikeybest(cfg["aikeybest_token"]) if is_enabled(cfg, "aikeybest") else disabled("aikeybest")
            except Exception as e:
                ak = None, str(e)
            try:
                nm = fetch_niumacode(cfg["niumacode_api_key"]) if is_enabled(cfg, "niumacode") else disabled("niumacode")
            except Exception as e:
                nm = None, str(e)
            try:
                km = fetch_kimi(cfg["kimi_api_key"]) if is_enabled(cfg, "kimi") else disabled("kimi")
            except Exception as e:
                km = None, str(e)
            try:
                glm = fetch_glm(cfg["glm_api_key"]) if is_enabled(cfg, "glm") else disabled("glm")
            except Exception as e:
                glm = None, str(e)
            try:
                cx = fetch_codex() if is_enabled(cfg, "codex") else disabled("codex")
            except Exception as e:
                cx = None, str(e)
            try:
                ts = fetch_typesafe(cfg.get("typesafe_cookie", "")) if is_enabled(cfg, "typesafe") else disabled("typesafe")
            except Exception as e:
                ts = None, str(e)
            self.result_queue.put((ds, mm, ak, nm, km, glm, cx, ts))

        def _build_menu(self):
            if hasattr(self, '_menu'):
                self._menu.clear()
            items = []
            # DeepSeek
            if not is_enabled(self.config, "deepseek"):
                pass
            elif self.ds_data:
                d = self.ds_data
                items.append(rumps.MenuItem(f"DeepSeek: ¥{d['total']} ({d['currency']})"))
                if float(d['granted']) > 0:
                    items.append(rumps.MenuItem(f"  Granted: ¥{d['granted']}"))
                items.append(rumps.MenuItem(f"  Topped up: ¥{d['topped_up']}"))
                items.append(rumps.MenuItem(f"  Status: {'Available' if d['available'] else 'Unavailable'}"))
            else:
                items.append(rumps.MenuItem(f"DeepSeek: {self.ds_error or 'N/A'}"))
            items.append(None)
            # MiniMax
            if not is_enabled(self.config, "minimax"):
                pass
            elif self.mm_data:
                items.append(rumps.MenuItem(f"MiniMax: {self.mm_data['avg_pct']}%"))
                for m in self.mm_data["models"]:
                    if m["name"] != "general":
                        continue
                    line = f"  general: week={m['weekly_pct']}%"
                    if m["reset_at"]:
                        line += f" (reset {m['reset_at']})"
                    items.append(rumps.MenuItem(line))
                    break
            else:
                items.append(rumps.MenuItem(f"MiniMax: {self.mm_error or 'N/A'}"))
            items.append(None)
            # AiKeyBest
            if not is_enabled(self.config, "aikeybest"):
                pass
            elif self.ak_data:
                available = self.ak_data.get("available", 0)
                total = self.ak_data.get("total", 0)
                pct = int(available / total * 100) if total > 0 else 0
                name = self.ak_data.get("name", "")
                items.append(rumps.MenuItem(f"AiKeyBest: {pct}% remaining"))
                if name:
                    items.append(rumps.MenuItem(f"  User: {name}"))
            else:
                items.append(rumps.MenuItem(f"AiKeyBest: {self.ak_error or 'N/A'}"))
            items.append(None)
            # NiuMaCode
            if not is_enabled(self.config, "niumacode"):
                pass
            elif self.nm_data:
                d = self.nm_data
                items.append(rumps.MenuItem(f"NM: {d['balance']:.2f} {d['unit']}"))
                if d.get("plan"):
                    items.append(rumps.MenuItem(f"  Plan: {d['plan']}"))
            else:
                items.append(rumps.MenuItem(f"NM: {self.nm_error or 'N/A'}"))
            items.append(None)
            # Kimi
            if is_enabled(self.config, "kimi") and self.km_data is not None:
                if self.km_data:
                    rows = self.km_data.get("rows", [])
                    if rows:
                        main = rows[0]
                        subscription = _kimi_subscription_usage(self.km_data)
                        if _kimi_total_quota_exhausted(self.km_data):
                            items.append(rumps.MenuItem("Kimi: OUT"))
                        else:
                            items.append(rumps.MenuItem(
                                f"Kimi Weekly: {100 - main['percent']}% remaining"))
                        if subscription:
                            items.append(rumps.MenuItem(
                                f"  Subscription: {subscription['used']}/{subscription['limit']} "
                                f"({100 - subscription['percent']}% remaining)"))
                        items.append(rumps.MenuItem(f"  {main['label']}: {main['used']}/{main['limit']}"))
                        if main['reset_at']:
                            items.append(rumps.MenuItem(f"  Reset: {main['reset_at']} ({main['countdown']})"))
                        for r in rows[1:]:
                            line = f"  {r['label']}: {100 - r['percent']}% ({r['used']}/{r['limit']})"
                            if r['reset_at']:
                                line += f" reset {r['reset_at']}"
                            items.append(rumps.MenuItem(line))
                    else:
                        items.append(rumps.MenuItem("Kimi: no data"))
                else:
                    items.append(rumps.MenuItem(f"Kimi: {self.km_error or 'N/A'}"))
            items.append(None)
            # GLM
            if is_enabled(self.config, "glm") and self.glm_data is not None:
                if self.glm_data:
                    items.append(rumps.MenuItem(f"GLM: {self.glm_data['pct']}% remaining"))
                    for ptype in self.glm_data.get("plans", []):
                        items.append(rumps.MenuItem(f"  {ptype}"))
                else:
                    items.append(rumps.MenuItem(f"GLM: {self.glm_error or 'N/A'}"))
            items.append(None)
            # Codex subscription
            if is_enabled(self.config, "codex"):
                if self.cx_data:
                    d = self.cx_data
                    title = "Codex: OUT" if d["exhausted"] else f"Codex: {d['remaining_pct']}% remaining"
                    items.append(rumps.MenuItem(title))
                    if d.get("account_name"):
                        items.append(rumps.MenuItem(f"  Account: {d['account_name']}"))
                    items.append(rumps.MenuItem(f"  Plan: {str(d['plan']).title()}"))
                    for label, window in (("Primary", d.get("primary")), ("Secondary", d.get("secondary"))):
                        if not window:
                            continue
                        line = f"  {label}: {window['used_pct']}% used"
                        if window.get("reset_at"):
                            line += f" (reset {window['reset_at']})"
                        items.append(rumps.MenuItem(line))
                    if d.get("credits_unlimited"):
                        items.append(rumps.MenuItem("  Credits: unlimited"))
                    elif d.get("credits_balance") is not None:
                        items.append(rumps.MenuItem(f"  Credits: {d['credits_balance']}"))
                else:
                    items.append(rumps.MenuItem(f"Codex: {self.cx_error or 'N/A'}"))
                items.append(None)
            # TypeSafe console balance
            if is_enabled(self.config, "typesafe"):
                if self.ts_data:
                    d = self.ts_data
                    items.append(rumps.MenuItem(f"TypeSafe: ${d['balance']:.2f}"))
                    items.append(rumps.MenuItem(f"  Spent this cycle: ${d['spent']:.2f}"))
                    if d.get("plan"):
                        items.append(rumps.MenuItem(f"  Plan: {d['plan']}"))
                    if d.get("cycle"):
                        items.append(rumps.MenuItem(f"  Cycle: {d['cycle']}"))
                    for credit in d.get("credits", []):
                        expiry = credit["expires_at"].split("T", 1)[0]
                        items.append(rumps.MenuItem(
                            f"  Credit: ${credit['remaining']:.2f}/${credit['amount']:.2f}, expires {expiry}"))
                else:
                    items.append(rumps.MenuItem(f"TypeSafe: {self.ts_error or 'N/A'}"))
                items.append(None)
            items.append(rumps.MenuItem("Refresh", callback=self.refresh))
            self.menu = items

        def update_title(self):
            parts = []
            if is_enabled(self.config, "deepseek"):
                if self.ds_data:
                    parts.append(f"DS:¥{self.ds_data['total']}")
                else:
                    parts.append("DS:--")
            if is_enabled(self.config, "minimax"):
                if self.mm_data:
                    parts.append(f"MM:{self.mm_data['avg_pct']}%")
                else:
                    parts.append("MM:--")
            if is_enabled(self.config, "aikeybest"):
                if self.ak_data:
                    available = self.ak_data.get("available", 0)
                    total = self.ak_data.get("total", 0)
                    pct = int(available / total * 100) if total > 0 else 0
                    parts.append(f"AK:{pct}%")
                else:
                    parts.append("AK:--")
            if is_enabled(self.config, "niumacode"):
                if self.nm_data:
                    parts.append(f"NM:{self.nm_data['balance']:.2f}")
                else:
                    parts.append("NM:--")
            # Kimi
            if is_enabled(self.config, "kimi") and self.km_data is not None:
                if self.km_data:
                    rows = self.km_data.get("rows", [])
                    if _kimi_total_quota_exhausted(self.km_data):
                        parts.append("KM:OUT")
                    elif rows:
                        parts.append(f"KM:{100 - rows[0]['percent']}%")
                    else:
                        parts.append("KM:--")
                else:
                    parts.append("KM:--")
            if is_enabled(self.config, "glm") and self.glm_data is not None:
                if self.glm_data:
                    parts.append(f"GLM:{self.glm_data['pct']}%")
                else:
                    parts.append("GLM:--")
            if is_enabled(self.config, "codex"):
                if self.cx_data:
                    parts.append("CX:OUT" if self.cx_data["exhausted"] else f"CX:{self.cx_data['remaining_pct']}%")
                else:
                    parts.append("CX:--")
            if is_enabled(self.config, "typesafe"):
                if self.ts_data:
                    parts.append(f"TS:${self.ts_data['balance']:.2f}")
                else:
                    parts.append("TS:--")
            self.title = " ".join(parts)


def print_status():
    config = load_config()
    print("=== LLM Balance Status ===\n")

    print("DeepSeek:")
    try:
        data, err = fetch_deepseek(config["deepseek_api_key"]) if is_enabled(config, "deepseek") else (None, "Disabled")
        if err:
            print(f"  Error: {err}")
        else:
            print(f"  Total: ¥{data['total']} ({data['currency']})")
            if float(data['granted']) > 0:
                print(f"  Granted: ¥{data['granted']}")
            print(f"  Topped up: ¥{data['topped_up']}")
            print(f"  Status: {'Available' if data['available'] else 'Unavailable'}")
    except Exception as e:
        print(f"  Error: {e}")

    print("\nMiniMax:")
    try:
        data, err = fetch_minimax(config["minimax_subscription_key"]) if is_enabled(config, "minimax") else (None, "Disabled")
        if err:
            print(f"  Error: {err}")
        else:
            print(f"  general week: {data['avg_pct']}%")
            for m in data["models"]:
                if m["name"] != "general":
                    continue
                line = f"  reset: {m['reset_at']}" if m["reset_at"] else ""
                if line:
                    print(line)
                break
    except Exception as e:
        print(f"  Error: {e}")

    print("\nNiuMaCode:")
    try:
        data, err = fetch_niumacode(config["niumacode_api_key"]) if is_enabled(config, "niumacode") else (None, "Disabled")
        if err:
            print(f"  Error: {err}")
        else:
            print(f"  Balance: {data['balance']:.2f} {data['unit']}")
            if data.get("plan"):
                print(f"  Plan: {data['plan']}")
    except Exception as e:
        print(f"  Error: {e}")

    print("\nAiKeyBest:")
    try:
        data, err = fetch_aikeybest(config["aikeybest_token"]) if is_enabled(config, "aikeybest") else (None, "Disabled")
        if err:
            print(f"  Error: {err}")
        else:
            available = data['available']
            total = data['total']
            pct = int(available / total * 100) if total > 0 else 0
            print(f"  Remaining: {pct}% ({format_number(available)}/{format_number(total)})")
            if data.get("name"):
                print(f"  User: {data['name']}")
    except Exception as e:
        print(f"  Error: {e}")

    print("\nKimi:")
    if not is_enabled(config, "kimi"):
        print("  Disabled")
    else:
        try:
            data, err = fetch_kimi(config["kimi_api_key"])
            if err:
                print(f"  Error: {err}")
            else:
                rows = data.get("rows", [])
                if not rows:
                    print("  No data")
                else:
                    main = rows[0]
                    subscription = _kimi_subscription_usage(data)
                    if subscription:
                        print(
                            f"  Subscription: {100 - subscription['percent']}% remaining "
                            f"({subscription['used']}/{subscription['limit']})")
                    else:
                        print("  Subscription: unavailable")
                    print(f"  {main['label']}: {100 - main['percent']}% remaining ({main['used']}/{main['limit']})")
                    if main['reset_at']:
                        print(f"  Reset: {main['reset_at']} ({main['countdown']})")
                    for r in rows[1:]:
                        line = f"  {r['label']}: {100 - r['percent']}% ({r['used']}/{r['limit']})"
                        if r['reset_at']:
                            line += f" reset {r['reset_at']}"
                        print(line)
        except Exception as e:
            print(f"  Error: {e}")

    print("\nGLM:")
    if not is_enabled(config, "glm"):
        print("  Disabled")
    else:
        try:
            data, err = fetch_glm(config["glm_api_key"])
            if err:
                print(f"  Error: {err}")
            else:
                print(f"  Remaining: {data['pct']}% ({format_number(data['used'])}/{format_number(data['limit'])})")
                for ptype in data.get("plans", []):
                    print(f"  - {ptype}")
        except Exception as e:
            print(f"  Error: {e}")

    print("\nCodex:")
    if not is_enabled(config, "codex"):
        print("  Disabled")
    else:
        try:
            data, err = fetch_codex()
            if err:
                print(f"  Error: {err}")
            else:
                status = "OUT" if data["exhausted"] else f"{data['remaining_pct']}% remaining"
                print(f"  Status: {status}")
                if data.get("account_name"):
                    print(f"  Account: {data['account_name']}")
                print(f"  Plan: {str(data['plan']).title()}")
                for label, window in (("Primary", data.get("primary")), ("Secondary", data.get("secondary"))):
                    if not window:
                        continue
                    line = f"  {label}: {window['used_pct']}% used"
                    if window.get("reset_at"):
                        line += f", reset {window['reset_at']}"
                    print(line)
        except Exception as e:
            print(f"  Error: {e}")

    print("\nTypeSafe:")
    if not is_enabled(config, "typesafe"):
        print("  Disabled")
    else:
        try:
            data, err = fetch_typesafe(config.get("typesafe_cookie", ""))
            if err:
                print(f"  Error: {err}")
            else:
                print(f"  Balance: ${data['balance']:.2f}")
                print(f"  Spent this cycle: ${data['spent']:.2f}")
                if data.get("plan"):
                    print(f"  Plan: {data['plan']}")
                if data.get("cycle"):
                    print(f"  Cycle: {data['cycle']}")
        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        if ensure_config():
            print(f"Config created at {CONFIG_FILE}")
            print("Please fill in your API keys and run again.")
            sys.exit(0)
        print_status()
        sys.exit(0)
    if rumps is None:
        sys.exit("rumps not installed; run via start.sh which uses uv")
    if ensure_config():
        rumps.alert(
            title="LLM Monitor",
            message=f"Config created at:\n{CONFIG_FILE}\n\nPlease fill in your API keys and restart."
        )
        sys.exit(0)
    app = LLMMonitorApp()
    app._build_menu()
    app.run()
