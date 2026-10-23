"""Use case: the aggregated, anonymous usage report (analyses per day; no identities, no images)."""
from __future__ import annotations

from mammo_application.abstractions import DailyUsage, UsageLog

MAX_REPORT_DAYS = 400


class UsageReportHandler:
    def __init__(self, usage: UsageLog) -> None:
        self._usage = usage

    def handle(self, days: int = 30) -> list[DailyUsage]:
        return self._usage.daily(max(1, min(days, MAX_REPORT_DAYS)))


__all__ = ["MAX_REPORT_DAYS", "UsageReportHandler"]
