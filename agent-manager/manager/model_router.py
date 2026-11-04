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

from .execution_policy import model_allowed
from .model_discovery import account_name, model_name, provider_runtime, triple_key
from .model_profiler import apply_outcome_smoothing, role_weight


@dataclass(frozen=True)
class RouteChoice:
    model_id: str
    provider: str
    score: float
    quota_confidence: str
    reasons: tuple[str, ...]


class NoEligibleModel(RuntimeError):
    """No compatible, available model can execute this step."""


def _runtime_of(model: Mapping) -> str:
    return str(model.get("runtime") or provider_runtime(model.get("provider"), model.get("cli")))


def _backend_allows(model: Mapping, backend: str) -> bool:
    if not model_allowed(model):
        return False
    provider = model.get("provider")
    runtime = _runtime_of(model)
    if backend == "ao":
        return provider == "codex"
    if backend == "cao":
        # CAO drives its native providers, and direct-fallback drives Claude-Code/OpenCode
        return (provider in {"codex", "antigravity", "ollama", "claude-code", "opencode", "evren"}
                and runtime in {"direct", "cao", "opencode", "claude-code"})
    return runtime in {"direct", "opencode", "claude-code", "cao"}


def _availability_ok(model_id: str, model: Mapping,
                     availability: Mapping[str, bool | None] | None) -> bool:
    if availability is None:
        return True
    runtime = _runtime_of(model)
    triple = str(model.get("triple_key") or triple_key(
        model_name(model) or model_id, runtime, account_name(model)))
    for key in (model_id, model.get("triple_key"), triple, runtime,
                model.get("provider"), model_name(model)):
        if key is not None and availability.get(str(key)) is False:
            return False
    return True


def _meets_requirements(model: dict, *, allow_writes: bool,
                        required_modalities: tuple[str, ...], required_tools: bool,
                        min_context_tokens: int) -> bool:
    """Reject only concrete task requirements; unknown required facts fail closed."""
    if allow_writes and model.get("supports_writes") is False:
        return False
    modalities = {str(value).casefold() for value in model.get("input_modalities", [])}
    if not set(required_modalities) <= modalities:
        return False
    if required_tools and not model.get(
        "mcp_supported", model.get("provider") in {"codex", "antigravity"}
    ):
        return False
    if min_context_tokens:
        capacity = model.get("context_window_tokens")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < min_context_tokens:
            return False
    return True


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

    # 4. Weekly quota without explicit duration -> 3 days
    if re.search(r"weekly|haftal[ıi]k", msg, re.IGNORECASE):
        return 259200.0

    # 5. Rate limit 429 without explicit time -> default 5 minutes
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

    def _key(self, model_id: str) -> tuple[str, str, str]:
        model = self.models.get(model_id, {})
        runtime = str(model.get("runtime") or provider_runtime(model.get("provider"), model.get("cli")))
        return (model_name(model) or model_id, runtime, account_name(model))

    def _key_map(self) -> dict[tuple[str, str, str], str]:
        return {self._key(mid): mid for mid in self.models}

    def _runtime_model_ids(self, model_id: str) -> tuple[str, ...]:
        """Triple peers share a bucket only inside the same (model, runtime).

        Effort variants of the same concrete model in one runtime share the
        provider bucket. The same model in another runtime is a separate row
        and never inherits this cooldown.
        """
        key = self._key(model_id)
        return tuple(mid for mid in self.models if self._key(mid) == key)

    def _quota_bucket_ids(self, model_id: str) -> tuple[str, ...]:
        """Model ids that share one quota with ``model_id`` for a quota rejection.

        Effort variants always share the (model, runtime, account) triple. On
        top of that, a catalog row can opt into account-scoped quota with
        ``shared_quota_account`` (set for subscription products like Codex and
        Claude Code), so one real quota rejection cools the whole family and
        stops the model-by-model cascade that stalls every capable model.
        """
        model = self.models.get(model_id, {})
        if model.get("shared_quota_account") is True:
            provider = model.get("provider")
            account = account_name(model)
            return tuple(mid for mid, peer in self.models.items()
                         if peer.get("provider") == provider
                         and account_name(peer) == account)
        return self._runtime_model_ids(model_id)

    def _public_status(self, row: Mapping, mid: str) -> dict:
        model = self.models.get(mid, {})
        return {
            "model_id": mid,
            "model": row.get("model_key", self._key(mid)[0]),
            "runtime": row.get("runtime", self._key(mid)[1]),
            "account": row.get("account", self._key(mid)[2]),
            "triple": triple_key(row.get("model_key", self._key(mid)[0]),
                                 row.get("runtime", self._key(mid)[1]),
                                 row.get("account", self._key(mid)[2])),
            "provider": model.get("provider"),
            "cooldown_until": row.get("cooldown_until"),
            "remaining_seconds": row.get("remaining_seconds"),
            "error_kind": row.get("error_kind"),
            "observed_at": row.get("observed_at"),
        }

    async def _init_db(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                existing_columns = []
                cursor = await db.execute("PRAGMA table_info(model_route_state)")
                existing_columns = [row[1] for row in await cursor.fetchall()]
                if existing_columns and "runtime" not in existing_columns:
                    # Migrate the old model-id primary key to a triple key.
                    await db.execute("ALTER TABLE model_route_state RENAME TO model_route_state_legacy")
                    existing_columns = []
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS model_route_state (
                        model_key TEXT NOT NULL,
                        runtime TEXT NOT NULL,
                        account TEXT NOT NULL,
                        cooldown_until TEXT,
                        error_kind TEXT,
                        remaining_fraction REAL,
                        reset_at TEXT,
                        quota_source TEXT,
                        quota_verified INTEGER NOT NULL DEFAULT 0,
                        observed_at TEXT,
                        PRIMARY KEY(model_key, runtime, account)
                    )
                """)
                if existing_columns == []:
                    try:
                        legacy = await (await db.execute(
                            "SELECT * FROM model_route_state_legacy")).fetchall()
                    except Exception:
                        legacy = []
                    for row in legacy:
                        old_id = str(row[0])
                        key = self._key(old_id)
                        await db.execute("""
                            INSERT OR REPLACE INTO model_route_state
                            (model_key,runtime,account,cooldown_until,error_kind,
                             remaining_fraction,reset_at,quota_source,quota_verified,observed_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?)
                        """, (*key, row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8]))
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS model_task_outcomes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        model_id TEXT NOT NULL,
                        task_kind TEXT NOT NULL,
                        passed INTEGER NOT NULL,
                        duration_seconds REAL NOT NULL,
                        total_tokens INTEGER NOT NULL,
                        recorded_at TEXT NOT NULL
                    )
                """)
                await db.execute("""CREATE INDEX IF NOT EXISTS idx_model_task_outcomes
                                  ON model_task_outcomes(task_kind, model_id, recorded_at)""")
                await db.commit()
            self._initialized = True

    async def record_outcome(self, model_id: str, task_kind: str, passed: bool,
                             duration_seconds: float, total_tokens: int,
                             *, verified: bool) -> None:
        """Record an externally checked result, never an agent's self-assessment."""
        if model_id not in self.models:
            raise ValueError("Bilinmeyen model")
        if not verified or not task_kind.strip() or len(task_kind) > 80:
            raise ValueError("Yalnız doğrulanmış ve türü belirtilmiş sonuç kaydedilir")
        if not isinstance(passed, bool) or not isinstance(total_tokens, int) or total_tokens < 0:
            raise ValueError("Geçersiz görev sonucu")
        if not isinstance(duration_seconds, (int, float)) or isinstance(duration_seconds, bool) \
                or not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("Geçersiz süre")
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""INSERT INTO model_task_outcomes
                (model_id,task_kind,passed,duration_seconds,total_tokens,recorded_at)
                VALUES(?,?,?,?,?,?)""",
                (model_id, task_kind.casefold().strip(), int(passed), float(duration_seconds),
                 total_tokens, datetime.now(timezone.utc).isoformat()))
            await db.commit()
        role = _ROLE_ALIASES.get(task_kind.casefold().strip(), task_kind.casefold().strip())
        self.models[model_id] = apply_outcome_smoothing(self.models[model_id], role, passed)

    async def _task_outcomes(self, task_kind: str) -> dict[str, tuple[int, int, float]]:
        if not task_kind:
            return {}
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            rows = await (await db.execute("""SELECT model_id,COUNT(*),SUM(passed),AVG(duration_seconds)
                FROM model_task_outcomes WHERE task_kind=? AND recorded_at>=?
                GROUP BY model_id""", (task_kind.casefold().strip(), since))).fetchall()
        return {str(mid): (int(count), int(passed), float(duration))
                for mid, count, passed, duration in rows}

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
        weekly = bool(re.search(r"weekly|haftal[ıi]k", message, re.IGNORECASE))
        if weekly:
            kind = "weekly_quota"
        if parsed_wait is not None:
            # A mis-parsed absolute "try again at" clock time must never jam a
            # model for days; bound it (24h, or up to 7 days for a weekly limit).
            parsed_wait = min(max(parsed_wait, 30.0), 7 * 86400.0 if weekly else 86400.0)
        seconds = parsed_wait or (300 if kind == "rate_limit" else (259200.0 if weekly else 1800))
        if retry_after_seconds is not None:
            if not math.isfinite(retry_after_seconds) or retry_after_seconds < 0:
                raise ValueError("Geçersiz retry_after_seconds")
            seconds = min(retry_after_seconds, 86400)
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await self._init_db()
        bucket = (self._quota_bucket_ids(model_id) if kind in {"quota", "weekly_quota"}
                  else self._runtime_model_ids(model_id))
        keys = {self._key(mid) for mid in bucket}
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("""
                INSERT INTO model_route_state(model_key,runtime,account,cooldown_until,error_kind,observed_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(model_key,runtime,account) DO UPDATE SET
                    cooldown_until=excluded.cooldown_until,
                    error_kind=excluded.error_kind,observed_at=excluded.observed_at
            """, [(*key, until.isoformat(), kind, datetime.now(timezone.utc).isoformat())
                  for key in keys])
            await db.commit()
        return kind

    async def record_success(self, model_id: str) -> None:
        if model_id not in self.models:
            raise ValueError(f"Bilinmeyen model: {model_id}")
        await self._init_db()
        keys = {self._key(mid) for mid in self._quota_bucket_ids(model_id)}
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("""
                INSERT INTO model_route_state(model_key,runtime,account,cooldown_until,error_kind)
                VALUES(?,?,?,NULL,NULL)
                ON CONFLICT(model_key,runtime,account) DO UPDATE SET
                    cooldown_until=NULL,error_kind=NULL,
                    remaining_fraction=NULL,reset_at=NULL,quota_verified=0
            """, [(*key,) for key in keys])
            await db.commit()

    async def clear_cooldown(self, model_id: str) -> None:
        """Clear cooldown across the model's quota bucket."""
        if model_id not in self.models:
            return
        await self._init_db()
        keys = {self._key(mid) for mid in self._quota_bucket_ids(model_id)}
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany("""
                INSERT INTO model_route_state(model_key,runtime,account,cooldown_until,error_kind)
                VALUES(?,?,?,NULL,NULL)
                ON CONFLICT(model_key,runtime,account) DO UPDATE SET
                    cooldown_until=NULL,error_kind=NULL,
                    remaining_fraction=NULL,reset_at=NULL,quota_verified=0
            """, [(*key,) for key in keys])
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
        key = self._key(model_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO model_route_state(model_key,runtime,account,remaining_fraction,reset_at,quota_source,
                    quota_verified,observed_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(model_key,runtime,account) DO UPDATE SET
                    remaining_fraction=excluded.remaining_fraction,reset_at=excluded.reset_at,
                    quota_source=excluded.quota_source,quota_verified=excluded.quota_verified,
                    observed_at=excluded.observed_at
            """, (*key, remaining_fraction,
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
        by_key: dict[tuple[str, str, str], list[str]] = {}
        for mid in self.models:
            by_key.setdefault(self._key(mid), []).append(mid)
        statuses = {}
        for row in rows:
            key = (str(row["model_key"]), str(row["runtime"]), str(row["account"]))
            # Effort variants share one triple bucket; every alias must see it.
            for mid in by_key.get(key, ()):
                statuses[mid] = dict(row)
        return statuses, usage

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
                        status = {**status, "cooldown_until": cooldown,
                                  "remaining_seconds": round(remaining_secs, 1),
                                  "error_kind": kind}
                        active[mid] = self._public_status(status, mid)
                except ValueError:
                    pass
        return active

    async def next_cooldown(self, role: str = "general", backend: str = "direct",
                            allow_writes: bool = False,
                            availability: Mapping[str, bool | None] | None = None,
                            required_modalities: tuple[str, ...] = (),
                            required_tools: bool = False,
                            min_context_tokens: int = 0,
                            hold_for_cooling_capable: bool = False) -> dict | None:
        """Earliest retry under the same suitability and optional hold policy as ranking."""
        if required_tools and backend != "cao":
            return None
        role = _ROLE_ALIASES.get(role.casefold(), role.casefold())
        required_modalities = tuple(str(value).casefold() for value in required_modalities)
        cooldowns = await self.get_cooldowns()
        pool = []
        for mid, model in self.models.items():
            if (mid == "codex-auto-review" or model.get("hidden") is True
                    or model.get("execution_supported", True) is not True
                    or not _backend_allows(model, backend)
                    or not _availability_ok(mid, model, availability)):
                continue
            capabilities = {str(value).casefold() for value in model.get("capabilities", [])}
            if role not in capabilities and not (role == "architect" and "master" in capabilities):
                continue
            if not _meets_requirements(model, allow_writes=allow_writes,
                                       required_modalities=required_modalities,
                                       required_tools=required_tools,
                                       min_context_tokens=min_context_tokens):
                continue
            pool.append((mid, model))
        ready = [(mid, model) for mid, model in pool if mid not in cooldowns]
        if (hold_for_cooling_capable and ready
                and all(model.get("provider") == "ollama" for _, model in ready)
                and any(mid in cooldowns and model.get("provider") != "ollama"
                        for mid, model in pool)):
            pool = [(mid, model) for mid, model in pool if model.get("provider") != "ollama"]
        # Healthy alternatives must run immediately unless the explicit writer
        # policy above holds local fallback for a compatible cooling remote.
        if any(mid not in cooldowns for mid, _ in pool):
            return None
        choices = [cooldowns[mid] for mid, _ in pool if mid in cooldowns]
        return min(choices, key=lambda row: row["cooldown_until"]) if choices else None

    async def rank(self, role: str = "general", backend: str = "direct",
                   allow_writes: bool = False, availability: Mapping[str, bool | None] | None = None,
                   exclude: tuple[str, ...] | list[str] | set[str] = (),
                   estimated_tokens: int = 0, required_modalities: tuple[str, ...] = (),
                   required_tools: bool = False, min_context_tokens: int = 0,
                   task_kind: str = "", hold_for_cooling_capable: bool = False) -> list[RouteChoice]:
        if backend not in {"direct", "ao", "cao"}:
            raise ValueError("Bilinmeyen yürütme altyapısı")
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens negatif olamaz")
        if min_context_tokens < 0:
            raise ValueError("min_context_tokens negatif olamaz")
        if required_tools and backend != "cao":
            return []
        required_modalities = tuple(str(value).casefold() for value in required_modalities)
        role = _ROLE_ALIASES.get(role.casefold(), role.casefold())
        statuses, usage = await self._signals()
        outcomes = await self._task_outcomes(task_kind)
        cooldowns = await self.get_cooldowns()
        now = datetime.now(timezone.utc)
        excluded = set(exclude)
        candidates = []
        for mid, model in self.models.items():
            if (mid in excluded or model.get("hidden") is True
                    or model.get("execution_supported", True) is not True
                    or mid == "codex-auto-review"
                    or not _backend_allows(model, backend)
                    or not _availability_ok(mid, model, availability)):
                continue
            capabilities = {str(value).casefold() for value in model.get("capabilities", [])}
            suitable = role in capabilities or (role == "architect" and "master" in capabilities)
            if not suitable:
                continue
            if not _meets_requirements(model, allow_writes=allow_writes,
                                       required_modalities=required_modalities,
                                       required_tools=required_tools,
                                       min_context_tokens=min_context_tokens):
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
        if hold_for_cooling_capable and candidates:
            def _capable(row: Mapping) -> bool:
                if (row.get("hidden") is True or row.get("execution_supported", True) is not True
                        or not _backend_allows(row, backend)):
                    return False
                caps = {str(value).casefold() for value in row.get("capabilities", [])}
                if not (role in caps or (role == "architect" and "master" in caps)):
                    return False
                return _meets_requirements(row, allow_writes=allow_writes,
                                           required_modalities=required_modalities,
                                           required_tools=required_tools,
                                           min_context_tokens=min_context_tokens)
            all_local = all(model.get("provider") == "ollama" for _, model, _ in candidates)
            cooling_remote = any(mid in cooldowns and _capable(model)
                                 for mid, model in self.models.items()
                                 if model.get("provider") != "ollama"
                                 and mid not in excluded
                                 and _availability_ok(mid, model, availability))
            if all_local and cooling_remote:
                return []
        max_usage = max((usage.get(mid, 0) for mid, _, _ in candidates), default=0)
        measured_durations = [outcomes[mid][2] for mid, _, _ in candidates
                              if mid in outcomes and outcomes[mid][0] >= 5 and outcomes[mid][2] > 0]
        fastest_measured = min(measured_durations) if len(measured_durations) >= 2 else None
        ranked = []
        for mid, model, status in candidates:
            provider = model["provider"]
            priority_role = "master" if role == "architect" and "architect" not in model.get("capabilities", []) else role
            priority = model.get("priority", {}).get(priority_role, 20)
            if not isinstance(priority, (float, int)) or isinstance(priority, bool) or not math.isfinite(priority):
                priority = 20
            # Normalize Codex / GPT priority so GPT models are competitively selected alongside Gemini and Claude
            if provider == "codex":
                # If this specific model is in cooldown, it is already excluded in candidates loop.
                # If there are widespread cooldowns on codex, softly demote healthy codex models
                codex_cooldown_count = sum(1 for mid, m in self.models.items() if m.get("provider") == "codex" and mid in cooldowns)
                if codex_cooldown_count >= 2:
                    priority = float(priority) + 20.0
                else:
                    priority = min(float(priority), 7.0)
            reasons = [f"{role} uyumu; katalog önceliği {priority:g}"]
            if not model.get("quality_verified", False) and not (outcomes.get(mid) and outcomes[mid][0] >= 5):
                reasons.append("görev kalitesi ölçülmedi; uygunluk operatör yapılandırması")
            calibrated_weight = role_weight(model, role)
            if calibrated_weight is None:
                score = float(priority)
                reasons.append("kalibre edilmedi; ağırlıksız")
            else:
                # Weight is success utility (higher is better), while ranking
                # minimizes cost. Keep it on the same scale as the catalog
                # prior: uncalibrated candidates receive the neutral 0.5 prior.
                score = float(priority) + (0.5 - calibrated_weight) * 24.0
                reasons.append(f"kalibre ağırlığı {calibrated_weight:.2f}; runtime {_runtime_of(model)}")
            sample = outcomes.get(mid)
            if sample and sample[0] >= 5:
                count, passed, mean_duration = sample
                # Shrink sparse observations toward an uninformative 50% prior.
                estimated_pass_rate = (passed + 2) / (count + 4)
                score += (0.5 - estimated_pass_rate) * 24
                if fastest_measured and mean_duration > fastest_measured:
                    score += min(4.0, math.log(mean_duration / fastest_measured) * 2)
                reasons.append(f"{task_kind} doğrulanmış sonuç: {passed}/{count}; ortalama {mean_duration:.1f} sn")
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
