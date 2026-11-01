"""Deterministic work ordering and timezone-aware proactive scheduling."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def _aware(moment: datetime | None) -> datetime:
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("Zaman değeri saat dilimi içermeli.")
    return moment.astimezone(timezone.utc)


def _due(item: dict) -> datetime | None:
    value = item.get("due_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _hhmm(value: str) -> int:
    hour, minute = str(value).split(":", 1)
    return int(hour) * 60 + int(minute)


def is_quiet_moment(moment: datetime, start: str = "22:00", end: str = "08:00") -> bool:
    current = moment.hour * 60 + moment.minute
    begin, finish = _hhmm(start), _hhmm(end)
    if begin == finish:
        return False
    if begin < finish:
        return begin <= current < finish
    return current >= begin or current < finish


def ordered_work(items, *, now: datetime | None = None, completed=()) -> list[dict]:
    """Order dependency-ready work by overdue state, deadline, priority and age.

    Work whose dependency is not completed is placed after its dependency in the
    same deterministic pass; cycles raise instead of silently dropping work.
    """
    now_utc = _aware(now)
    done = {str(item) for item in completed}
    records = [dict(item) for item in items]
    by_id = {str(record.get("id")): record for record in records if record.get("id")}
    for record in records:
        if record.get("status") == "done":
            done.add(str(record.get("id")))
    remaining = set(by_id)
    result: list[dict] = []
    while remaining:
        ready = [by_id[wid] for wid in remaining
                 if set(by_id[wid].get("dependencies") or []) <= done]
        if not ready:
            raise ValueError("İş bağımlılıklarında döngü veya eksik bağımlılık var.")
        ready.sort(key=lambda record: (
            _due(record) or datetime.max.replace(tzinfo=timezone.utc),
            PRIORITY_RANK.get(record.get("priority", "normal"), 2),
            str(record.get("created_at", "")),
            str(record.get("id", "")),
        ))
        for record in ready:
            wid = str(record.get("id"))
            record["blocked_by"] = []
            record["overdue"] = bool(_due(record) and _due(record) <= now_utc)
            result.append(record)
            done.add(wid)
            remaining.discard(wid)
    return result


def next_window(now: datetime, *, timezone_name: str = "", hour: int = 9,
                quiet_start: str = "22:00", quiet_end: str = "08:00") -> datetime:
    """Return the next allowed local run time; honors quiet hours and timezone."""
    if now.tzinfo is None:
        raise ValueError("Zaman değeri saat dilimi içermeli.")
    try:
        zone = ZoneInfo(timezone_name) if timezone_name else now.tzinfo
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Bilinmeyen saat dilimi.") from exc
    local = now.astimezone(zone)
    candidate = local.replace(hour=max(0, min(23, int(hour))), minute=0,
                              second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    for _ in range(4):
        if not is_quiet_moment(candidate, quiet_start, quiet_end):
            break
        end_minutes = _hhmm(quiet_end)
        candidate = candidate.replace(hour=end_minutes // 60, minute=end_minutes % 60,
                                      second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def missed_run(trigger: dict, *, now: datetime | None = None,
               max_grace_seconds: int = 86_400) -> dict:
    """Decide how to resume a proactive trigger after the PC was offline."""
    now_utc = _aware(now)
    next_run = trigger.get("next_run_at")
    if not next_run:
        return {"missed": False, "reason": "Zamanlanmış çalışma yok."}
    try:
        planned = datetime.fromisoformat(str(next_run))
    except ValueError:
        return {"missed": False, "reason": "Geçersiz zaman."}
    if planned.tzinfo is None:
        planned = planned.replace(tzinfo=timezone.utc)
    planned = planned.astimezone(timezone.utc)
    if planned > now_utc:
        return {"missed": False, "planned_at": planned.isoformat()}
    late = (now_utc - planned).total_seconds()
    if late > max_grace_seconds:
        return {"missed": True, "should_run": False, "late_seconds": int(late),
                "reason": "Kaçırılan çalışma çok eski; bir kez telafi edilip sonraki pencereye geçilecek."}
    return {"missed": True, "should_run": True, "late_seconds": int(late),
            "reason": "Bilgisayar kapalıyken kaçırılan çalışma telafi edilecek."}


def event_key(kind: str, identifier: str, version: str = "") -> str:
    raw = f"{kind}:{identifier}:{version}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def proactive_decision(event: dict, rules: dict, *, now: datetime | None = None) -> dict:
    """Explain whether a proactive event should start work, with a dedupe key."""
    now_utc = _aware(now)
    key = event_key(str(event.get("kind", "")), str(event.get("id", "")),
                    str(event.get("version", "")))
    if not rules.get("enabled", True):
        return {"act": False, "reason": "Proaktif çalışma kapalı.", "dedupe_key": key}
    if not event.get("id"):
        return {"act": False, "reason": "Olay kimliği yok.", "dedupe_key": key}
    if event.get("seen_key") == key:
        return {"act": False, "reason": "Bu olay daha önce işlendi.", "dedupe_key": key}
    tz_name = rules.get("timezone", "")
    try:
        zone = ZoneInfo(tz_name) if tz_name else now_utc.tzinfo
    except ZoneInfoNotFoundError:
        zone = now_utc.tzinfo
    local = now_utc.astimezone(zone)
    start = rules.get("quiet_start", "22:00")
    end = rules.get("quiet_end", "08:00")
    allowed_hour = int(rules.get("hour", 9))
    if is_quiet_moment(local, start, end):
        return {"act": False, "reason": "Sessiz saatler; çalışma ertelendi.",
                "dedupe_key": key,
                "run_at": next_window(now_utc, timezone_name=tz_name,
                                      hour=allowed_hour, quiet_start=start,
                                      quiet_end=end).isoformat()}
    budget = int(rules.get("daily_budget", 0))
    used = int(rules.get("used_today", 0))
    if budget and used >= budget:
        return {"act": False, "reason": "Günlük proaktif çalışma bütçesi doldu.",
                "dedupe_key": key}
    return {"act": True, "reason": "Yeni olay ilgili işi yeniden değerlendiriyor.",
            "dedupe_key": key, "run_at": now_utc.isoformat()}
