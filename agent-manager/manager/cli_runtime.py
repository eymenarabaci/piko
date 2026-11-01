"""CLI runtime manifest registry, capability negotiation and quota selection.

The engine must not know which CLI is installed. It reads a runtime manifest:

* ``probe``: a ``PATH`` lookup plus a non-interactive ``--version`` call,
* ``capabilities``: resume, MCP and a non-interactive approval flag,
* ``adapter``: the call signature that turns a request into an argv plan.

A model identity is the triple ``(model, runtime, account)``. The same model can
run through two CLIs with separate quotas, so routing, queues and metrics key on
the triple rather than on the model name alone.

This module never starts an interactive service, a pty or a background daemon.
Probing only resolves an executable and runs its version command.

Quota values are caller observations, never invented here. Real provider
adapters and quota pollers are wired later; search for ``# INTEGRATION:``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Iterable, Mapping, Protocol, Sequence

CAPABILITY_NAMES = ("resume", "mcp", "noninteractive")
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION_PATTERN = re.compile(
    r"(?<![\d.])(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?)"
)
_NEVER = datetime.max.replace(tzinfo=timezone.utc)


class UnknownRuntimeError(KeyError):
    """The requested CLI runtime is not in the registry."""


@dataclass(frozen=True, slots=True)
class ModelKey:
    """A model is only addressable together with its runtime and account."""

    model: str
    runtime: str
    account: str

    def __post_init__(self) -> None:
        for name in ("model", "runtime", "account"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ModelKey.{name} bos olamaz.")
            object.__setattr__(self, name, value.strip())

    def public(self) -> dict:
        return {"model": self.model, "runtime": self.runtime, "account": self.account}


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a runtime can do, and which flags make it safe to call unattended.

    ``noninteractive_flag`` is the approval flag that suppresses interactive
    prompts. It is only meaningful when ``noninteractive`` is true.
    """

    resume: bool = False
    mcp: bool = False
    noninteractive: bool = False
    noninteractive_flag: tuple[str, ...] = ()
    sandbox_flag: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.noninteractive and not self.noninteractive_flag:
            raise ValueError(
                "noninteractive yetenegi icin noninteractive_flag gerekli."
            )

    def approval_flags(self) -> tuple[str, ...]:
        return self.noninteractive_flag if self.noninteractive else ()

    def public(self) -> dict:
        return {
            "resume": self.resume,
            "mcp": self.mcp,
            "noninteractive": self.noninteractive,
            "noninteractive_flag": list(self.noninteractive_flag),
            "sandbox_flag": list(self.sandbox_flag),
        }


@dataclass(frozen=True, slots=True)
class RuntimeManifest:
    """Declarative description of one CLI working environment."""

    id: str
    title: str
    executables: tuple[str, ...]
    version_args: tuple[str, ...] = ("--version",)
    capabilities: Capabilities = field(default_factory=Capabilities)
    adapter: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID_PATTERN.match(self.id):
            raise ValueError(f"Gecersiz runtime kimligi: {self.id!r}")
        if not self.executables or not all(
            isinstance(name, str) and name.strip() for name in self.executables
        ):
            raise ValueError("executables en az bir bos olmayan ad icermeli.")
        if not self.version_args:
            raise ValueError("version_args en az bir bayrak icermeli.")
        if not self.adapter:
            object.__setattr__(self, "adapter", self.id)

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "executables": list(self.executables),
            "version_args": list(self.version_args),
            "capabilities": self.capabilities.public(),
            "adapter": self.adapter,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Outcome of a manifest probe. Missing is a fact, not an error."""

    runtime_id: str
    available: bool
    executable: str | None = None
    version: str | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)
    detail: str = ""

    def public(self) -> dict:
        return {
            "runtime_id": self.runtime_id,
            "available": self.available,
            "executable": self.executable,
            "version": self.version,
            "capabilities": self.capabilities.public(),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    ok: bool
    missing: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def public(self) -> dict:
        return {
            "ok": self.ok,
            "missing": list(self.missing),
            "reasons": list(self.reasons),
        }


class RuntimeRegistry:
    def __init__(self, manifests: Iterable[RuntimeManifest] = ()) -> None:
        self._manifests: dict[str, RuntimeManifest] = {}
        for manifest in manifests:
            self.register(manifest)

    def register(self, manifest: RuntimeManifest) -> RuntimeManifest:
        if not isinstance(manifest, RuntimeManifest):
            raise TypeError("Yalnizca RuntimeManifest kaydedilebilir.")
        if manifest.id in self._manifests:
            raise ValueError(f"Runtime zaten kayitli: {manifest.id}")
        self._manifests[manifest.id] = manifest
        return manifest

    def get(self, runtime_id: str) -> RuntimeManifest:
        try:
            return self._manifests[runtime_id]
        except KeyError as exc:
            raise UnknownRuntimeError(runtime_id) from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._manifests)

    def manifests(self) -> tuple[RuntimeManifest, ...]:
        return tuple(self._manifests.values())

    def probe_all(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] | None = None,
        runner: Callable[..., str] | None = None,
        timeout: float = 10.0,
    ) -> tuple[ProbeResult, ...]:
        return tuple(
            probe(
                manifest,
                environ=environ,
                which=which,
                runner=runner,
                timeout=timeout,
            )
            for manifest in self._manifests.values()
        )


def parse_version(text: str | None) -> str | None:
    if not text:
        return None
    match = _VERSION_PATTERN.search(text)
    return match.group(1) if match else None


def _default_which(environ: Mapping[str, str]) -> Callable[[str], str | None]:
    search_path = environ.get("PATH", "")

    def find(name: str) -> str | None:
        return shutil.which(name, path=search_path)

    return find


def _version_argv(executable: str, version_args: Sequence[str]) -> list[str]:
    argv = [executable, *version_args]
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd", "/c", *argv]
    return argv


def _default_runner(argv: Sequence[str], *, timeout: float = 10.0) -> str:
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    return (completed.stdout or "") + (completed.stderr or "")


def probe(
    manifest: RuntimeManifest,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., str] | None = None,
    timeout: float = 10.0,
) -> ProbeResult:
    """Resolve the executable on PATH and read its version read-only.

    A version command failure still leaves the runtime ``available``: discovery
    and version reporting are separate facts. Nothing interactive is launched.
    """
    env = os.environ if environ is None else environ
    finder = which or _default_which(env)
    executable = None
    for name in manifest.executables:
        found = finder(name)
        if found:
            executable = found
            break
    if not executable:
        return ProbeResult(
            runtime_id=manifest.id,
            available=False,
            capabilities=manifest.capabilities,
            detail="PATH uzerinde bulunamadi.",
        )
    run = runner or _default_runner
    try:
        output = run(
            _version_argv(executable, manifest.version_args), timeout=timeout
        )
    except Exception as exc:  # noqa: BLE001 - probe must never crash the caller
        return ProbeResult(
            runtime_id=manifest.id,
            available=True,
            executable=executable,
            capabilities=manifest.capabilities,
            detail=f"Surum okunamadi: {type(exc).__name__}",
        )
    version = parse_version(output)
    return ProbeResult(
        runtime_id=manifest.id,
        available=True,
        executable=executable,
        version=version,
        capabilities=manifest.capabilities,
        detail="" if version else "Surum metni taninmadi.",
    )


def negotiate(
    manifest: RuntimeManifest, *, require: Iterable[str] = ()
) -> CapabilityDecision:
    """Check requested capabilities against a manifest without side effects."""
    wanted = tuple(dict.fromkeys(str(name).strip().lower() for name in require))
    unknown = sorted(set(wanted) - set(CAPABILITY_NAMES))
    if unknown:
        raise ValueError(f"Bilinmeyen yetenek: {', '.join(unknown)}")
    missing = tuple(
        name
        for name in CAPABILITY_NAMES
        if name in wanted and not getattr(manifest.capabilities, name)
    )
    reasons = tuple(f"{name} desteklenmiyor" for name in missing)
    return CapabilityDecision(ok=not missing, missing=missing, reasons=reasons)


# --- Call adapters -----------------------------------------------------------
#
# Adapters are pure planner functions: they turn a request into an argv plan and
# never spawn a process. The real CLI invocations are wired in a later phase.


@dataclass(frozen=True, slots=True)
class InvocationRequest:
    prompt: str
    workspace: str
    allow_writes: bool = False
    timeout: float = 600.0
    effort: str = ""
    images: tuple[str, ...] = ()
    on_event: Callable[[dict], None] | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class InvocationPlan:
    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str] = field(default_factory=dict)
    stdin_text: str | None = None
    interactive: bool = False

    def __post_init__(self) -> None:
        if self.interactive:
            raise ValueError("Etkilesimli runtime baslatma bu katmanda yasak.")
        if not self.argv:
            raise ValueError("argv bos olamaz.")


AdapterSignature = Callable[[ModelKey, InvocationRequest], InvocationPlan]


def _unwired_adapter(key: ModelKey, request: InvocationRequest) -> InvocationPlan:
    # INTEGRATION: replace with the real argv builder for this runtime. The
    # engine only ever calls this signature; it must stay non-interactive.
    raise NotImplementedError(
        f"'{key.runtime}' cagri adaptoru henuz baglanmadi."
    )


ADAPTER_SIGNATURES: Mapping[str, AdapterSignature] = {
    "opencode": _unwired_adapter,
    "claude-code": _unwired_adapter,
    "cao": _unwired_adapter,
}


def adapter_signature(manifest: RuntimeManifest) -> AdapterSignature:
    try:
        return ADAPTER_SIGNATURES[manifest.adapter]
    except KeyError as exc:
        raise UnknownRuntimeError(manifest.adapter) from exc


# --- Quota buckets -----------------------------------------------------------


class QuotaBucket(Protocol):
    """Read-only quota view for one ``(model, runtime, account)`` triple.

    ``remaining`` is a 0..1 observed fraction. ``window_seconds`` is the quota
    period (the window) when known. ``reset_at`` is timezone-aware or ``None``
    when the provider does not expose a reset. No value is invented here.
    """

    key: ModelKey
    remaining: float
    window_seconds: float | None
    reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class BucketSnapshot:
    """Simple concrete bucket used by probes, tools and tests."""

    key: ModelKey
    remaining: float
    reset_at: datetime | None = None
    window_seconds: float | None = None
    source: str = "observed"

    def __post_init__(self) -> None:
        if not isinstance(self.key, ModelKey):
            raise TypeError("BucketSnapshot.key bir ModelKey olmali.")
        value = float(self.remaining)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("remaining 0 ile 1 arasinda sonlu bir oran olmali.")
        if self.reset_at is not None and self.reset_at.tzinfo is None:
            raise ValueError("reset_at saat dilimi icermeli.")
        if self.window_seconds is not None and (
            not math.isfinite(self.window_seconds) or self.window_seconds <= 0
        ):
            raise ValueError("window_seconds pozitif olmali.")

    def public(self) -> dict:
        return {
            "key": self.key.public(),
            "remaining": self.remaining,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "window_seconds": self.window_seconds,
            "source": self.source,
        }


def bucket_remaining(bucket: QuotaBucket) -> float:
    try:
        value = float(bucket.remaining)
    except (AttributeError, TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def is_empty(bucket: QuotaBucket) -> bool:
    """An exhausted or unreadable bucket is skipped, never guessed at."""
    return bucket_remaining(bucket) <= 0.0


def _reset_key(bucket: QuotaBucket) -> datetime:
    reset = getattr(bucket, "reset_at", None)
    if reset is None:
        return _NEVER
    if reset.tzinfo is None:
        raise ValueError("reset_at saat dilimi icermeli.")
    return reset


def _matches(
    bucket: QuotaBucket,
    *,
    model: str | None,
    runtime: str | None,
    account: str | None,
) -> bool:
    key = bucket.key
    return (
        (model is None or key.model == model)
        and (runtime is None or key.runtime == runtime)
        and (account is None or key.account == account)
    )


def rank_buckets(
    buckets: Iterable[QuotaBucket],
    *,
    model: str | None = None,
    runtime: str | None = None,
    account: str | None = None,
    now: datetime | None = None,
) -> list[QuotaBucket]:
    """Skip empty buckets and order the rest by reset time, then by remaining.

    ``None`` resets sort last because waiting on an unknown reset is worse than
    switching to a triple whose quota is known to come back. ``now`` is accepted
    for callers that filter expired observations themselves; it does not invent
    quota values.
    """
    del now  # kept in the signature for a future reset-aware policy
    live = [
        bucket
        for bucket in buckets
        if not is_empty(bucket) and _matches(
            bucket, model=model, runtime=runtime, account=account
        )
    ]
    return sorted(
        live,
        key=lambda bucket: (
            _reset_key(bucket),
            -bucket_remaining(bucket),
            bucket.key.model,
            bucket.key.runtime,
            bucket.key.account,
        ),
    )


def choose_bucket(
    buckets: Iterable[QuotaBucket],
    *,
    model: str | None = None,
    runtime: str | None = None,
    account: str | None = None,
    now: datetime | None = None,
) -> QuotaBucket | None:
    """Pick the first usable bucket; ``None`` means wait or ask the user."""
    ranked = rank_buckets(
        buckets,
        model=model,
        runtime=runtime,
        account=account,
        now=now,
    )
    return ranked[0] if ranked else None


def next_reset(
    buckets: Iterable[QuotaBucket],
    *,
    model: str | None = None,
    runtime: str | None = None,
    account: str | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """Earliest future reset among matching buckets, for a wait-vs-switch call."""
    moment = now or datetime.now(timezone.utc)
    upcoming = [
        reset
        for bucket in buckets
        if _matches(bucket, model=model, runtime=runtime, account=account)
        if (reset := getattr(bucket, "reset_at", None)) is not None
        and reset > moment
    ]
    return min(upcoming) if upcoming else None


_LIVE_MANIFESTS = (
    RuntimeManifest(
        id="opencode",
        title="OpenCode CLI",
        executables=("opencode.exe", "opencode.cmd", "opencode"),
        version_args=("--version",),
        capabilities=Capabilities(
            resume=True,
            mcp=True,
            noninteractive=True,
            noninteractive_flag=("--yes",),
        ),
        notes="Surum ve onay bayragi gercek kurulumda dogrulanacak.",
    ),
    RuntimeManifest(
        id="claude-code",
        title="Claude Code CLI",
        executables=("claude.exe", "claude.cmd", "claude"),
        version_args=("--version",),
        capabilities=Capabilities(
            resume=True,
            mcp=True,
            noninteractive=True,
            noninteractive_flag=("--dangerously-skip-permissions",),
        ),
        notes="Onay bayragi yalnizca izole workspace icinde kullanilmali.",
    ),
    RuntimeManifest(
        id="cao",
        title="CAO vendored engine",
        executables=("cao.exe", "cao"),
        version_args=("--version",),
        capabilities=Capabilities(
            resume=True,
            mcp=True,
            noninteractive=True,
            noninteractive_flag=("--yes",),
        ),
        notes="Gomulu CAO; probe asla daemon baslatmaz.",
    ),
)

REGISTRY = RuntimeRegistry(_LIVE_MANIFESTS)


def get_manifest(runtime_id: str) -> RuntimeManifest:
    return REGISTRY.get(runtime_id)


def probe_all(
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., str] | None = None,
    timeout: float = 10.0,
) -> tuple[ProbeResult, ...]:
    return REGISTRY.probe_all(
        environ=environ, which=which, runner=runner, timeout=timeout
    )
