"""Usage counters per day in a small JSON file, written atomically. Nothing personal is kept: counts per day, plus,
for the current day only, keyed hashes of who analyzed so that a person counts once; those hashes are dropped when
the day ends and cannot be reversed without the server's secret."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from dataclasses import fields
from datetime import datetime, timezone as utc_zone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mammo_application.abstractions import DailyUsage, UsageEvent

log = logging.getLogger(__name__)

_LEVELS = ("high_priority", "review", "no_suspicious_findings")
_COUNTS = tuple(f.name for f in fields(DailyUsage) if f.name != "day")


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


class JsonUsageLog:
    def __init__(self, path: Path, secret: bytes, timezone: str = "America/Bogota", keep_days: int = 400,
                 today=None) -> None:
        self._path, self._secret, self._keep = path, secret, keep_days
        try:
            self._zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:  # e.g. Windows without the tzdata package
            log.warning("Unknown time zone %s: usage days are counted in UTC.", timezone)
            self._zone = utc_zone.utc
        self._today = today or (lambda: datetime.now(self._zone).date().isoformat())
        self._lock = threading.Lock()
        self._state = self._load()
        self._roll_over(self._today())

    def _load(self) -> dict:
        empty = {"days": {}, "current": {"day": None, "people": []}}
        try:
            state = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty
        except Exception as error:  # unreadable or not JSON: start empty rather than keep the server from starting
            log.warning("Usage counters unreadable, starting empty (%s).", type(error).__name__)
            return empty
        days = state.get("days") if isinstance(state, dict) else None
        current = state.get("current") if isinstance(state, dict) else None
        if not isinstance(days, dict) or not isinstance(current, dict) or not isinstance(current.get("people"), list):
            log.warning("Usage counters malformed, starting empty.")
            return empty
        return {"days": {d: c for d, c in days.items() if isinstance(d, str) and isinstance(c, dict)},
                "current": {"day": current.get("day"), "people": [p for p in current["people"] if isinstance(p, str)]}}

    def _save(self) -> None:
        temporary = self._path.with_name(self._path.name + ".tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as file:
                json.dump(self._state, file, separators=(",", ":"))
                file.flush()
                os.fsync(file.fileno())  # a power cut must not leave an empty file that then replaces the history
            os.replace(temporary, self._path)
        except OSError as error:
            log.warning("Could not save the usage counters (%s).", type(error).__name__)

    def _roll_over(self, day: str) -> dict:
        """The current day's person hashes; those of an earlier day are dropped for good (and the file rewritten)."""
        current = self._state["current"]
        if current.get("day") != day:
            stale = bool(current.get("people"))
            self._state["current"] = current = {"day": day, "people": []}
            if stale:
                self._save()
        return current

    def _person(self, day: str, requester: str) -> str:
        message = f"{day}|{requester.strip().lower()}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()[:16]

    def record(self, event: UsageEvent) -> None:
        try:
            with self._lock:
                day = self._today()
                current = self._roll_over(day)
                counts = self._state["days"].setdefault(day, {})
                counts["analyses"] = counts.get("analyses", 0) + 1
                outcome = event.outcome if event.outcome in _LEVELS + ("not_a_mammogram",) else "errors"
                counts[outcome] = counts.get(outcome, 0) + 1
                counts["views"] = counts.get("views", 0) + event.views
                counts["detailed_views"] = counts.get("detailed_views", 0) + event.detailed_views
                if event.requester and "@" in event.requester:  # people, not service tokens
                    person = self._person(day, event.requester)
                    if person not in current["people"]:
                        current["people"].append(person)
                        counts["people"] = counts.get("people", 0) + 1
                for old in sorted(self._state["days"])[:-self._keep]:
                    del self._state["days"][old]
                self._save()
        except Exception as error:  # counting must never break an analysis
            log.warning("Could not count an analysis (%s).", type(error).__name__)

    def daily(self, days: int) -> list[DailyUsage]:
        with self._lock:
            self._roll_over(self._today())
            recent = sorted(self._state["days"].items(), reverse=True)[:days]
            return [DailyUsage(day, **{k: _count(v.get(k)) for k in _COUNTS}) for day, v in recent]


class NoUsageLog:
    """When no usage file is configured (local runs, stdio): nothing is counted."""

    def record(self, event: UsageEvent) -> None:
        pass

    def daily(self, days: int) -> list[DailyUsage]:
        return []


__all__ = ["JsonUsageLog", "NoUsageLog"]
