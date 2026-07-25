"""
Slack / Teams Notifications — Capability #10

Sends a structured run summary to a Slack Incoming Webhook or
Microsoft Teams Incoming Webhook after every test session.

Config keys: SLACK_WEBHOOK_URL, TEAMS_WEBHOOK_URL, SLACK_CHANNEL
"""

import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Post test run summaries to Slack using Block Kit layout."""

    def __init__(self, webhook_url: str, channel: str = "#qa-alerts"):
        self._url     = webhook_url
        self._channel = channel

    def send_run_summary(
        self,
        passed: int,
        failed: int,
        skipped: int,
        error: int,
        duration: float,
        run_ts: str,
        report_url: Optional[str] = None,
        ai_summary: Optional[str] = None,
        flaky_tests: Optional[list] = None,
    ) -> bool:
        """Post a formatted run summary. Returns True on success."""
        total   = passed + failed + skipped + error
        status  = "✅ PASSED" if failed == 0 and error == 0 else "❌ FAILED"
        colour  = "#36a64f" if failed == 0 and error == 0 else "#E01E5A"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Playwright Test Run — {status}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Run ID:*\n`{run_ts}`"},
                    {"type": "mrkdwn", "text": f"*Duration:*\n{duration:.1f}s"},
                    {"type": "mrkdwn", "text": f"*Total:* {total}"},
                    {"type": "mrkdwn", "text": f"*Passed:* {passed}  |  *Failed:* {failed}  |  *Skipped:* {skipped}"},
                ],
            },
        ]

        if ai_summary:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*AI Summary:*\n{ai_summary}"},
            })

        if flaky_tests:
            flaky_text = "\n".join(
                f"• `{t['test_id']}` — {t['flake_rate']*100:.0f}% flake rate"
                for t in flaky_tests[:5]
            )
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*⚠ Flaky Tests:*\n{flaky_text}"},
            })

        if report_url:
            blocks.append({
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View HTML Report"},
                    "url": report_url,
                    "style": "primary",
                }],
            })

        payload = {
            "channel":     self._channel,
            "attachments": [{"color": colour, "blocks": blocks}],
        }

        return self._post(payload, "Slack")

    def send_text(self, text: str) -> bool:
        """Post a plain markdown message (used for regression alerts)."""
        payload = {"channel": self._channel,
                   "blocks": [{"type": "section",
                               "text": {"type": "mrkdwn", "text": text}}]}
        return self._post(payload, "Slack")

    def _post(self, payload: dict, platform: str) -> bool:
        try:
            resp = requests.post(
                self._url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("%s notification sent.", platform)
                return True
            logger.warning("%s notification failed: %d %s", platform, resp.status_code, resp.text)
            return False
        except Exception as exc:
            logger.error("%s notification error: %s", platform, exc)
            return False


class TeamsNotifier:
    """Post test run summaries to Microsoft Teams via Incoming Webhook."""

    def __init__(self, webhook_url: str):
        self._url = webhook_url

    def send_run_summary(
        self,
        passed: int,
        failed: int,
        skipped: int,
        duration: float,
        run_ts: str,
        ai_summary: Optional[str] = None,
    ) -> bool:
        status   = "PASSED ✅" if failed == 0 else "FAILED ❌"
        colour   = "Good" if failed == 0 else "Attention"
        facts    = [
            {"name": "Run ID",   "value": run_ts},
            {"name": "Passed",   "value": str(passed)},
            {"name": "Failed",   "value": str(failed)},
            {"name": "Skipped",  "value": str(skipped)},
            {"name": "Duration", "value": f"{duration:.1f}s"},
        ]
        if ai_summary:
            facts.append({"name": "AI Summary", "value": ai_summary[:300]})

        payload = {
            "@type":       "MessageCard",
            "@context":    "https://schema.org/extensions",
            "themeColor":  "36a64f" if failed == 0 else "E01E5A",
            "summary":     f"Test Run: {status}",
            "sections": [{
                "activityTitle":    f"Playwright Test Run — {status}",
                "activitySubtitle": f"Run: {run_ts}",
                "facts":            facts,
                "markdown":         True,
            }],
        }
        try:
            resp = requests.post(self._url, json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as exc:
            logger.error("Teams notification error: %s", exc)
            return False
