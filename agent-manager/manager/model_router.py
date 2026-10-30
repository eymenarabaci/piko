"""Capability-first model routing with observed quota signals and honest unknowns.

CLI executable discovery and token usage are not provider quota APIs. A verified
remaining fraction can be supplied by a real adapter; until then routing uses
observed quota errors and recent usage only as conservative availability hints.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
from typing import Mapping

import aiosqlite


@dataclass(frozen=True)
class RouteChoice:
    model_id: str
    provider: str
    score: float
    quota_confidence: str
    reasons: tuple[str, ...]


class NoEligibleModel(RuntimeError):
    """No compatible, available model can execute this step."""


_QUOTA_ERROR = re.compile(
    r"quota[_ -]?exceeded|insufficient[_ -]?quota|usage limit|kullanım kotası|"
    r"quota exhausted|quota.{0,30}(?:exceed|reach|limit)|"
    r"exceeded your current quota|credit(?:s)? exhausted|resource[_ -]?exhausted|"
    r"try\s+again\s+at|resets?\s+in", re.IGNORECASE,
)
_RATE_ERROR = re.compile(
    r"rate[_ -]?limit|too many requests|istek sınırı|\b429\b", re.IGNORECASE,
)


def parse_quota_wait_seconds(message: str) -> float | None:
    """Extract wait time in seconds from error message if available.

    Supports:
    - 'try again at 2:43 PM' / 'try again at 14:43'
    - 'try again in 2h 30m' / 'resets in 45m' / 'retry in 120s'
    - 429 / rate limit without explicit time -> default 300s (5 minutes)
    """
    if not message:
        return None
    msg = message.replace("\n", " ")

    # 1. 'try again at HH:MM AM/PM' or 'try again at HH:MM'
    m_at = re.search(r"try\s+again\s+at\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?", msg, re.IGNORECASE)
    if m_at:
        h = int(m_at.group(1))
        m = int(m_at.group(2))
        s = int(m_at.group(3) or 0)
        ampm = (m_at.group(4) or "").upper()
        if ampm == "PM" and h < 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=s, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return max(1.0, (target - now).total_seconds())

    # 2. Compound durations such as "Resets in 111h7m37s".
    duration = re.search(r"(?:try\s+again\s+in|resets?\s+in|retry\s+in|wait)\s+((?:\d+(?:\.\d+)?\s*(?:hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\s*)+)", msg, re.IGNORECASE)
    if duration:
        units = re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)", duration.group(1), re.IGNORECASE)
        total = sum(float(value) * (3600 if unit.lower().startswith("h") else
                                     60 if unit.lower().startswith("m") else 1)
                    for value, unit in units)
        if total > 0:
            return total

    # 3. 'in X hours/minutes/seconds' or 'resets in X' or 'retry in X'
    m_in = re.search(
        r"(?:try\s+again\s+in|resets?\s+in|retry\s+in|wait)\s+(\d+(?:\.\d+)?)\s*(h(?:our)?s?|m(?:in(?:ute)?)?s?|s(?:ec(?:ond)?)?s?)",
        msg, re.IGNORECASE,
    )
    if m_in:
        val = float(m_in.group(1))
        unit = m_in.group(2).lower()
        if unit.startswith("h"):
            return val * 3600.0
        elif unit.startswith("m"):
            return val * 60.0
        elif unit.startswith("s"):
            return val

    # 4. Rate limit 429 without explicit time -> default 5 minutes
    if re.search(r"rate[_ -]?limit|\b429\b|too\s+many\s+requests", msg, re.IGNORECASE):
        return 300.0

    return None
_ROLE_ALIASES = {
    "developer": "development", "implementation": "development", "coding": "development",
    "planner": "architect", "planning": "architect", "task_decomposition": "architect",
    "planlama": "architect", "mimari": "architect",
    "review": "reviewer", "qa": "reviewer", "testing": "reviewer", "inceleme": "reviewer",
    "writing": "general", "writer": "general", "text": "general", "yazı": "general",
    "orchestrator": "master", "reasoning": "master",
}


def infer_role(persona: str, prompt: str = "") -> str:
    """Choose a broad catalog capability; never infer provider-specific skill."""
    persona_value = persona.casefold()
    # The declared worker role takes precedence over incidental words in its
    # task title (e.g. "event planning" is still implementation work).
    for keywords, role in (
        (("review", "audit", "security", "test", "qa"), "reviewer"),
        (("develop", "frontend", "backend", "code", "implement"), "development"),
        (("architect", "planner", "research", "analy"), "architect"),
        (("orchestrat",), "master"),
    ):
        if any(word in persona_value for word in keywords):
            return role
    value = prompt[:400].casefold()
    for keywords, role in (
        (("review", "audit", "security", "test", "qa", "incele", "denet"), "reviewer"),
        (("plan", "architect", "mimar", "tasarım", "decompos"), "architect"),
        (("orchestrat", "koordina", "yönetic"), "master"),
        (("develop", "frontend", "backend", "code", "implement", "kod", "geliştir"), "development"),
    ):
        if any(word in value for word in keywords):
            return role
    return "general"


class ModelRouter:
    """Persist observed failures/quota snapshots beside the existing token ledger.

    The caller owns provider health probing and explicit quota snapshots. This
    class never invents a numeric account quota or treats CLI presence as proof
    that a remote model can run.
    """

    def __init__(self, models: list[dict] | Mapping[str, dict], ledger_path: Path | str):
        rows = models.values() if isinstance(models, Mapping) else models
        self.models = {row["id"]: dict(row) for row in rows}
        self.db_path = Path(ledger_path)
        self._init_lock = asyncio.Lock()
        self._initialized = False

    def _runtime_model_ids(self, model_id: str) -> tuple[str, ...]:
        """Variants of one CLI model, shared provider CLI quota, or model family share a cooldown.

        - Codex CLI shares a single account quota across all its models.
        - Antigravity models share quotas by family (all gemini-* models share Gemini quota;
          all claude-* models share Claude quota).
        """
        model = self.models.get(model_id, {})
        provider = model.get("provider")
        runtime_id = model.get("base_model") or model_id

        if provider == "codex":
            return tuple(mid for mid, candidate in self.models.items()
                         if candidate.get("provider") == "codex")

        if provider == "antigravity":
            base = str(model.get("base_model") or runtime_id).lower()
            return tuple(mid for mid, candidate in self.models.items()
                         if candidate.get("provider") == "antigravity"
                         and str(candidate.get("base_model") or mid).lower() == base)

        return tuple(mid for mid, candidate in self.models.items()
                     if candidate.get("provider") == provider
                     and (candidate.get("base_model") or mid) == runtime_id)

    async def _init_db(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS model_route_state (
                        model_id TEXT PRIMARY KEY,
                        cooldown_until TEXT,
                        error_kind TEXT,
                        remaining_fraction REAL,
                        reset_at TEXT,
                        quota_source TEXT,
                        quota_verified INTEGER NOT NULL DEFAULT 0,
                        observed_at TEXT
                    )
                """)
                await db.commit()
            self._initialized = True

    async def record_failure(self, model_id: str, error: BaseException | str,
                             retry_after_seconds: float | None = None) -> str | None:
        """Back off quota/rate errors; return their kind or None for other errors.

        Default durations are local retry backoff, not a claimed provider reset.
        """
        if model_id not in self.models:
            raise ValueError(f"Bilinmeyen model: {model_id}")
        message = str(error)
        kind = "quota" if _QUOTA_ERROR.search(message) else "rate_limit" if _RATE_ERROR.search(message) else None
        if kind is None:
            return None
        parsed_wait = parse_quota_wait_seconds(message)
        seconds = parsed_wait or (1800 if kind == "quota" else 300)
        if retry_after_seconds is not None:
            if not math.isfinite(retry_after_seconds) or retry_after_seconds < 0:
                raise ValueError("Geçersiz retry_after_seconds")
            seconds = min(retry_after_seconds, 86400)
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("""
                INSERT INTO model_route_state(model_id,cooldown_until,error_kind,observed_at)
                VALUES(?,?,?,?)
                ON CONFLICT(model_id) DO UPDATE SET cooldown_until=excluded.cooldown_until,
                    error_kind=excluded.error_kind,observed_at=excluded.observed_at
            """, [(mid, until.isoformat(), kind, datetime.now(timezone.utc).isoformat())
                  for mid in self._runtime_model_ids(model_id)])
            await db.commit()
        return kind

    async def record_success(self, model_id: str) -> None:
        if model_id not in self.models:
            raise ValueError(f"Bilinmeyen model: {model_id}")
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("""
                INSERT INTO model_route_state(model_id,cooldown_until,error_kind)
                VALUES(?,NULL,NULL)
                ON CONFLICT(model_id) DO UPDATE SET cooldown_until=NULL,error_kind=NULL,
                    remaining_fraction=NULL,reset_at=NULL,quota_verified=0
            """, [(mid,) for mid in self._runtime_model_ids(model_id)])
            await db.commit()

    async def clear_cooldown(self, model_id: str) -> None:
        """Clear cooldown for a model and its runtime variants."""
        if model_id not in self.models:
            return
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("""
                INSERT INTO model_route_state(model_id,cooldown_until,error_kind)
                VALUES(?,NULL,NULL)
                ON CONFLICT(model_id) DO UPDATE SET cooldown_until=NULL,error_kind=NULL,
                    remaining_fraction=NULL,reset_at=NULL,quota_verified=0
            """, [(mid,) for mid in self._runtime_model_ids(model_id)])
            await db.commit()

    async def clear_all_cooldowns(self) -> None:
        """Clear all active cooldowns across all models."""
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE model_route_state SET cooldown_until=NULL, error_kind=NULL,
                    remaining_fraction=NULL, reset_at=NULL, quota_verified=0
            """)
            await db.commit()

    async def record_quota_snapshot(self, model_id: str, remaining_fraction: float | None,
                                    reset_at: datetime | None, source: str,
                                    verified: bool) -> None:
        """Accept only a caller-supplied observation, never a fabricated estimate."""
        if model_id not in self.models:
            raise ValueError(f"Bilinmeyen model: {model_id}")
        if remaining_fraction is not None and (
            not math.isfinite(remaining_fraction) or not 0 <= remaining_fraction <= 1
        ):
            raise ValueError("remaining_fraction 0 ile 1 arasında olmalı")
        if reset_at is not None and reset_at.tzinfo is None:
            raise ValueError("reset_at saat dilimi içermeli")
        if verified and (remaining_fraction is None or not source.strip()):
            raise ValueError("Doğrulanmış kota için kaynak ve oran gerekli")
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO model_route_state(model_id,remaining_fraction,reset_at,quota_source,
                    quota_verified,observed_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(model_id) DO UPDATE SET
                    remaining_fraction=excluded.remaining_fraction,reset_at=excluded.reset_at,
                    quota_source=excluded.quota_source,quota_verified=excluded.quota_verified,
                    observed_at=excluded.observed_at
            """, (model_id, remaining_fraction,
                  reset_at.astimezone(timezone.utc).isoformat() if reset_at else None,
                  source[:100], int(verified), datetime.now(timezone.utc).isoformat()))
            await db.commit()

    async def _signals(self) -> tuple[dict[str, dict], dict[str, int]]:
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM model_route_state")).fetchall()
            table = await (await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_ledger' ")).fetchone()
            usage = {}
            if table:
                since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                values = await (await db.execute(
                    "SELECT model_id,COALESCE(SUM(total_tokens),0) FROM token_ledger "
                    "WHERE timestamp >= ? GROUP BY model_id", (since,)
                )).fetchall()
                usage = {str(row[0]): int(row[1]) for row in values}
        return {str(row["model_id"]): dict(row) for row in rows}, usage

    async def get_cooldowns(self) -> dict[str, dict]:
        """Return active cooldowns and quota details for all models."""
        statuses, _ = await self._signals()
        now = datetime.now(timezone.utc)
        active = {}
        for mid, status in statuses.items():
            cooldown = status.get("cooldown_until")
            kind = status.get("error_kind", "quota")
            if (not cooldown and status.get("quota_verified") and
                    status.get("remaining_fraction") is not None and
                    float(status["remaining_fraction"]) <= 0 and status.get("reset_at")):
                cooldown = status["reset_at"]
                kind = "verified_quota"
            if cooldown:
                try:
                    dt = datetime.fromisoformat(cooldown)
                    if dt > now:
                        remaining_secs = (dt - now).total_seconds()
                        active[mid] = {
                            "model_id": mid,
                            "cooldown_until": cooldown,
                            "remaining_seconds": round(remaining_secs, 1),
                            "error_kind": kind,
                            "observed_at": status.get("observed_at"),
                        }
                except ValueError:
                    pass
        return active

    async def next_cooldown(self, role: str = "general", backend: str = "direct",
                            allow_writes: bool = False,
                            availability: Mapping[str, bool | None] | None = None) -> dict | None:
        """Earliest retry for a compatible model; never relax capability or backend rules."""
        role = _ROLE_ALIASES.get(role.casefold(), role.casefold())
        cooldowns = await self.get_cooldowns()
        choices = []
        for mid, model in self.models.items():
            provider = model.get("provider")
            if (mid == "codex-auto-review" or model.get("execution_supported", True) is not True
                    or provider not in {"codex", "antigravity", "ollama"}
                    or (backend == "ao" and provider != "codex")
                    or (backend == "cao" and provider not in {"codex", "antigravity", "ollama"})
                    or (availability is not None and
                        (availability.get(mid) is False or availability.get(provider) is False))):
                continue
            capabilities = {str(value).casefold() for value in model.get("capabilities", [])}
            if role not in capabilities and not (role == "architect" and "master" in capabilities):
                continue
            if mid in cooldowns:
                choices.append(cooldowns[mid])
        return min(choices, key=lambda row: row["cooldown_until"]) if choices else None

    async def rank(self, role: str = "general", backend: str = "direct",
                   allow_writes: bool = False, availability: Mapping[str, bool | None] | None = None,
                   exclude: tuple[str, ...] | list[str] | set[str] = (),
                   estimated_tokens: int = 0) -> list[RouteChoice]:
        if backend not in {"direct", "ao", "cao"}:
            raise ValueError("Bilinmeyen yürütme altyapısı")
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens negatif olamaz")
        role = _ROLE_ALIASES.get(role.casefold(), role.casefold())
        statuses, usage = await self._signals()
        cooldowns = await self.get_cooldowns()
        now = datetime.now(timezone.utc)
        excluded = set(exclude)
        candidates = []
        for mid, model in self.models.items():
            provider = model.get("provider")
            if mid in excluded or model.get("execution_supported", True) is not True or mid == "codex-auto-review":
                continue
            if backend == "ao" and provider != "codex":
                continue
            if backend == "cao" and provider not in {"codex", "antigravity", "ollama"}:
                continue
            if provider not in {"codex", "antigravity", "ollama"}:
                continue
            if availability is not None and (availability.get(mid) is False or availability.get(provider) is False):
                continue
            capabilities = {str(value).casefold() for value in model.get("capabilities", [])}
            suitable = role in capabilities or (role == "architect" and "master" in capabilities)
            if not suitable:
                continue
            status = statuses.get(mid, {})
            if mid in cooldowns:
                continue
            cooldown = status.get("cooldown_until")
            if cooldown:
                try:
                    datetime.fromisoformat(cooldown)
                except ValueError:
                    continue  # Corrupt status fails closed for this candidate.
            candidates.append((mid, model, status))

        if not candidates:
            return []
        max_usage = max((usage.get(mid, 0) for mid, _, _ in candidates), default=0)
        ranked = []
        for mid, model, status in candidates:
            provider = model["provider"]
            priority_role = "master" if role == "architect" and "architect" not in model.get("capabilities", []) else role
            priority = model.get("priority", {}).get(priority_role, 20)
            if not isinstance(priority, (float, int)) or isinstance(priority, bool) or not math.isfinite(priority):
                priority = 20
            # Normalize Codex / GPT priority so GPT models are competitively selected alongside Gemini and Claude
            if provider == "codex":
                priority = min(float(priority), 7.0)
            reasons = [f"{role} uyumu; katalog önceliği {priority:g}"]
            if not model.get("quality_verified", False):
                reasons.append("görev kalitesi ölçülmedi; uygunluk operatör yapılandırması")
            score = float(priority)
            verified = bool(status.get("quota_verified"))
            observed = status.get("observed_at")
            # Snapshots older than 15 minutes do not claim live remaining quota.
            if verified and observed:
                try:
                    verified = datetime.fromisoformat(observed) >= now - timedelta(minutes=15)
                except ValueError:
                    verified = False
            else:
                verified = False
            remaining = status.get("remaining_fraction") if verified else None
            if remaining is not None:
                score += (1 - float(remaining)) * 12
                confidence = "verified"
                reasons.append(f"doğrulanmış kalan kota: %{round(100 * float(remaining))}")
                if estimated_tokens and remaining <= 0.05:
                    score += 8  # The exact account token allowance is unknown.
            elif provider == "ollama":
                confidence = "local"
                reasons.append("yerel Ollama; sağlayıcı API kotası yok")
            else:
                confidence = "unknown"
                reasons.append("sağlayıcı kalan kotası bilinmiyor")
                score += 3
            used = usage.get(mid, 0)
            if max_usage and used:
                pressure = math.log1p(used) / math.log1p(max_usage)
                score += pressure * 6
                reasons.append(f"son 24 saatte ölçülen yaklaşık kullanım: {used} token")
            ranked.append(RouteChoice(mid, provider, round(score, 4), confidence, tuple(reasons)))
        return sorted(ranked, key=lambda row: (row.score, row.model_id))

    async def choose(self, *args, **kwargs) -> RouteChoice:
        choices = await self.rank(*args, **kwargs)
        if not choices:
            raise NoEligibleModel("Göreve uygun, kullanılabilir model bulunamadı.")
        return choices[0]
