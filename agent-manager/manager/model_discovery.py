"""Discover model identities as ``(model, runtime, account)`` triples.

A model name alone is not enough: the same model can run through OpenCode,
Claude Code, CAO or a direct provider and every one of those paths has its own
quota. Discovery therefore produces one row per triple.

No network call is made here. The only external command is ``opencode models``
(read-only, bounded) plus the existing runtime version probes. If a discovery
command does not answer within the timeout the runtime is skipped: a partly
known catalog is better than a hung refresh.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Sequence

from . import cli_runtime

RUNTIMES = ("opencode", "claude-code", "cao", "direct")
DEFAULT_ACCOUNT = "default"
_OPENCODE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]*$")
_CLAUDE_MODELS = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def provider_runtime(provider: str | None, cli: str | None = None) -> str:
    """Map a legacy provider/catalog row to its runtime bucket."""
    value = (provider or "").casefold().strip()
    cli_value = (cli or "").casefold().strip()
    if value == "opencode" or cli_value == "opencode":
        return "opencode"
    if value in {"claude-code", "claude_code", "claude"} or cli_value == "claude":
        return "claude-code"
    if value == "cao" or cli_value == "cao":
        return "cao"
    return "direct"


def model_name(record: Mapping) -> str:
    return str(record.get("model") or record.get("base_model") or record.get("id") or "").strip()


def account_name(record: Mapping) -> str:
    return str(record.get("account") or DEFAULT_ACCOUNT).strip() or DEFAULT_ACCOUNT


def triple_id(model: str, runtime: str, account: str = DEFAULT_ACCOUNT) -> str:
    """Stable catalog id for a triple.

    Legacy direct rows keep their historical id so existing selections and
    saved plans remain valid. Any non-direct runtime is namespaced because the
    same model name can exist in more than one runtime.
    """
    model = model.strip()
    if runtime == "direct":
        return model
    return f"{runtime}:{model}"


def triple_key(model: str, runtime: str, account: str = DEFAULT_ACCOUNT) -> str:
    return f"{model}::{runtime}::{account}"


def key_of(record: Mapping) -> tuple[str, str, str]:
    return (model_name(record), provider_runtime(record.get("provider"), record.get("cli"))
            if not record.get("runtime") else str(record["runtime"]),
            account_name(record))


def normalize_record(record: Mapping) -> dict:
    """Return a copy with explicit triple fields, preserving user state."""
    row = dict(record)
    runtime = str(row.get("runtime") or provider_runtime(row.get("provider"), row.get("cli")))
    model = model_name(row) or str(row.get("id") or "")
    account = account_name(row)
    row["model"] = model
    row["runtime"] = runtime
    row["account"] = account
    row.setdefault("base_model", model)
    row.setdefault("hidden", False)
    row.setdefault("execution_supported", True)
    # Subscription products bill quota per account, so one rejection cools the
    # whole family. Discovery and refresh must not strip this operator choice.
    if row.get("provider") in {"codex", "claude-code"}:
        row["shared_quota_account"] = True
    row["triple_key"] = triple_key(model, runtime, account)
    return row


def _default_find(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    path = None if environ is None else environ.get("PATH")
    return shutil.which(name, path=path)


def _cli_argv(executable: str, args: Sequence[str]) -> list[str]:
    argv = [executable, *args]
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


def parse_opencode_models(output: str) -> list[str]:
    """Parse ``opencode models`` output, one ``provider/model`` per line."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in (output or "").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        token = line.split()[0]
        if "/" not in token:
            continue
        if not _OPENCODE_TOKEN.match(token):
            continue
        if token not in seen:
            seen.add(token)
            found.append(token)
    return found


def discover_opencode(
    *,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., str] | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> list[dict]:
    finder = which or (lambda name: _default_find(name, environ))
    executable = None
    for name in ("opencode.exe", "opencode.cmd", "opencode"):
        executable = finder(name)
        if executable:
            break
    if not executable:
        return []
    run = runner or _default_runner
    try:
        output = run(_cli_argv(executable, ("models",)), timeout=timeout)
    except (subprocess.TimeoutExpired, TimeoutError, OSError, ValueError):
        return []
    rows = []
    for name in parse_opencode_models(output):
        provider = name.split("/", 1)[0].casefold()
        rows.append(_discovered_row(
            name=name,
            runtime="opencode",
            provider=provider,
            cli="opencode",
            source="opencode models",
            note="OpenCode CLI model listesinden keşfedildi; kimlik (model, runtime, hesap) üçlüsüdür.",
        ))
    return rows


def discover_claude_code(
    *,
    available: bool,
    runtime_id: str = "claude-code",
) -> list[dict]:
    if not available:
        return []
    return [
        _discovered_row(
            name=name,
            runtime=runtime_id,
            provider=runtime_id,
            cli="claude",
            source="claude-code runtime",
            note="Claude Code kurulumu bulundu; desteklenen Claude ailesi runtime üçlüsü olarak eklendi.",
        )
        for name in _CLAUDE_MODELS
    ]


def discover_cao(models: Iterable[Mapping], *, available: bool) -> list[dict]:
    if not available:
        return []
    rows = []
    seen: set[str] = set()
    for record in models:
        provider = str(record.get("provider") or "")
        if provider not in {"codex", "antigravity", "ollama"}:
            continue
        name = model_name(record)
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(_discovered_row(
            name=name,
            runtime="cao",
            provider=provider,
            cli="cao",
            source="cao runtime",
            note="CAO kurulumu bulundu; aynı model CAO runtime kotasıyla ayrı satırdır.",
        ))
    return rows


def discover_direct(models: Iterable[Mapping], *, environ: Mapping[str, str] | None = None) -> list[dict]:
    """Keep API-key/direct catalog rows as explicit ``runtime=direct`` triples."""
    env = os.environ if environ is None else environ
    rows = []
    for record in models:
        runtime = provider_runtime(record.get("provider"), record.get("cli"))
        if runtime != "direct":
            continue
        row = normalize_record(record)
        row["runtime"] = "direct"
        row["triple_key"] = triple_key(row["model"], "direct", row["account"])
        rows.append(row)
    # API keys are never probed from the network. Their presence only marks a
    # runtime bucket as direct-capable for already-configured models.
    _ = env
    return rows


def _discovered_row(*, name: str, runtime: str, provider: str, cli: str | None,
                    source: str, note: str) -> dict:
    from .model_profiler import derive_capabilities

    capabilities, priority = derive_capabilities({"model": name, "runtime": runtime})
    return {
        "id": triple_id(name, runtime),
        "model": name,
        "base_model": name,
        "provider": provider,
        "runtime": runtime,
        "account": DEFAULT_ACCOUNT,
        "cli": cli,
        "capabilities": capabilities,
        "effort_levels": [],
        "priority": priority,
        "availability": {"status": "unknown", "quota_status": "unknown",
                         "checked_at": None, "source": None, "verified": False},
        "provenance": {
            "identity": "cli_discovery" if runtime != "direct" else "configured_catalog",
            "capabilities": "derived_metadata",
            "provider_catalog_verified": runtime == "opencode",
            "calibrated": False,
            "note": note,
        },
        "default_effort": "",
        "hidden": False,
        "execution_supported": True,
        "catalog_source": source,
        "catalog_observed_at": utc_now(),
        "quantization": None,
        "parameter_size": None,
        "digest": None,
        "input_modalities": ["text", "image"] if "vision" in name.casefold() or "/vl" in name.casefold() else ["text"],
        "triple_key": triple_key(name, runtime),
    }


def probe_runtimes(
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., str] | None = None,
    timeout: float = 10.0,
) -> dict[str, bool]:
    try:
        results = cli_runtime.probe_all(environ=environ, which=which, runner=runner, timeout=timeout)
    except Exception:
        return {runtime_id: False for runtime_id in cli_runtime.REGISTRY.ids()}
    return {result.runtime_id: bool(result.available) for result in results}


def discover_models(
    root_dir: Path | str,
    *,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., str] | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: float = 10.0,
    available: Mapping[str, bool] | None = None,
) -> list[dict]:
    """Discover triples for every installed runtime plus configured direct rows."""
    root = Path(root_dir)
    existing = load_models_file(root / "config" / "models.json")
    probe = dict(available) if available is not None else probe_runtimes(
        environ=environ, which=which, runner=runner, timeout=timeout)
    # Providers.available already knows OpenCode; the manifest probe can still
    # be false when only a shim exists. Treat either signal as discoverable.
    if which is not None or environ is not None:
        probe["opencode"] = bool(probe.get("opencode")) or bool(
            (which or (lambda name: _default_find(name, environ)))("opencode"))
    rows: list[dict] = []
    rows.extend(discover_opencode(which=which, runner=runner, environ=environ, timeout=timeout))
    rows.extend(discover_claude_code(available=bool(probe.get("claude-code"))))
    rows.extend(discover_cao(existing, available=bool(probe.get("cao"))))
    rows.extend(discover_direct(existing, environ=environ))
    return rows


def load_models_file(path: Path | str) -> list[dict]:
    file = Path(path)
    if not file.exists() and file.name == "models.json":
        file = file.with_name("models.example.json")
    if not file.exists():
        return []
    try:
        payload = json.loads(file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    rows = payload.get("models", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict) and row.get("id")]


_NEVER_OVERWRITE = ("hidden", "role_weights", "calibrated_at", "calibration")


def merge_models(existing: Iterable[Mapping], discovered: Iterable[Mapping]) -> list[dict]:
    """Id-based merge; user hidden/calibration state always wins."""
    merged = [normalize_record(row) for row in existing]
    by_id = {row["id"]: index for index, row in enumerate(merged)}
    for raw in discovered:
        row = normalize_record(raw)
        index = by_id.get(row["id"])
        if index is None:
            row["hidden"] = bool(row.get("hidden", False))
            by_id[row["id"]] = len(merged)
            merged.append(row)
            continue
        current = merged[index]
        preserved = {key: current[key] for key in _NEVER_OVERWRITE if key in current}
        updated = {**current, **row}
        updated.update(preserved)
        # A user-hidden triple stays hidden even when fresh discovery says
        # visible. A visible triple is not silently re-hidden by discovery.
        updated["hidden"] = bool(current.get("hidden", False))
        merged[index] = updated
    return merged


def save_models_file(path: Path | str, models: Iterable[Mapping], *,
                     schema_version: int = 1) -> Path:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    rows = [normalize_record(row) for row in models]
    payload = {"schema_version": schema_version, "models": rows}
    temporary = file.with_suffix(file.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(file)
    return file


def refresh_models_file(path: Path | str, discovered: Iterable[Mapping]) -> tuple[list[dict], int]:
    """Merge discovery into the on-disk catalog and return (rows, new_count)."""
    file = Path(path)
    existing = load_models_file(file)
    before = {row["id"] for row in existing}
    merged = merge_models(existing, discovered)
    save_models_file(file, merged)
    new_count = sum(1 for row in merged if row["id"] not in before)
    return merged, new_count
