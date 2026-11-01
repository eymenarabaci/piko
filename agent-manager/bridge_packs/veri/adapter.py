"""The "veri" bridge pack: local dataset profiling, report drafts and evidence.

Levels
------
``read``
    Parse one CSV/TSV/JSON dataset inside the workspace, profile every column
    (type, non-null count, distinct count, numeric min/max/mean) and return a
    deterministic three-line summary.
``draft``
    Build a Markdown report plus CSV schema/group summaries **in memory**.
    Drafting writes nothing to disk.
``apply``
    Write the approved draft files into a target folder inside the workspace
    and return evidence: file list, per-file sha256, a three-line summary and a
    readable report preview.

There is deliberately no ``dangerous`` level. This adapter only creates new
report files: it never deletes, moves or overwrites source data, and an apply
target that already contains the report is refused unless ``overwrite=True`` is
passed explicitly.

The live-provider swap point is this file only: :func:`load_rows` and
:func:`read_dataset` hide where the bytes come from. Tests use local fixture
files in a temporary workspace; no network call is made.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

BRIDGE_ID = "veri"
EVIDENCE_FORMAT = "veri-evidence"
MAX_READ_BYTES = 2_000_000
MAX_ROWS = 5_000
PREVIEW_ROWS = 5
SAMPLE_VALUES = 3
DEFAULT_BUDGET = {
    "profile": "veri-default",
    "scope": "bridge:veri",
    "token_limit": 40_000,
    "seconds_limit": 900,
    "repairs_limit": 2,
}
MANAGER_PERSONA = {"role": "scheduler",
                   "bridges": {BRIDGE_ID: {"levels": ["read", "draft"]}}}
APPROVER_PERSONA = {"role": "implementer",
                    "bridges": {BRIDGE_ID: {"levels": ["read", "draft", "apply"]}}}
PROACTIVE_PERSONA = {"role": "reviewer",
                     "bridges": {BRIDGE_ID: {"levels": ["read"]}}}


class VeriError(ValueError):
    """Base error that also names its taxonomy class."""

    error_class = "needs-user"


class RetryableVeriError(VeriError):
    error_class = "retryable"


class ExternalVeriError(VeriError):
    error_class = "external-down"


def error_payload(exc: Exception) -> dict:
    return {"bridge": BRIDGE_ID,
            "error_class": getattr(exc, "error_class", "needs-user"),
            "message": str(exc)[:800]}


def _three_line(lines, fallback: str) -> str:
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    return "\n".join(cleaned[:3]) if cleaned else fallback


def _root(workspace) -> Path:
    if not str(workspace or "").strip():
        raise VeriError("Calisma alani gerekli.")
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise VeriError("Calisma alani mevcut bir dizin olmali.")
    return root


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _resolve(root: Path, relative: str) -> Path:
    relative = str(relative or "").strip()
    if not relative:
        return root
    target = (root / relative).resolve()
    if not _inside(root, target):
        raise VeriError("Yol calisma alani disinda; reddedildi.")
    return target


def _load_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ExternalVeriError(f"Kaynak okunamadi: {exc}") from exc


def _name(value, index: int) -> str:
    name = str(value or "").strip()
    return name or f"kolon_{index}"


def _normalize_rows(header: list[str], body: list[list[str]]) -> list[dict]:
    rows: list[dict] = []
    for raw in body:
        cells = list(raw) + [""] * (len(header) - len(raw))
        rows.append({header[index]: str(cells[index])
                     for index in range(len(header))})
    return rows


def _parse_csv(data: bytes, delimiter: str) -> tuple[list[str], list[dict]]:
    text = data.decode("utf-8-sig", errors="replace")
    parsed = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not parsed:
        raise VeriError("Kaynak bos; kolon basligi bulunamadi.")
    header = [_name(cell, index + 1) for index, cell in enumerate(parsed[0])]
    if not any(header):
        raise VeriError("Kaynak bos; kolon basligi bulunamadi.")
    return header, _normalize_rows(header, parsed[1:])


def _parse_json(data: bytes) -> tuple[list[str], list[dict]]:
    try:
        payload = json.loads(data.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        raise VeriError(f"JSON cozumlenemedi: {exc.msg}.") from exc
    if isinstance(payload, dict):
        for key in ("rows", "data", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise VeriError("JSON kaynagi bos bir liste olmali.")
    if all(isinstance(item, dict) for item in payload):
        header: list[str] = []
        for item in payload:
            for key in item:
                if str(key) not in header:
                    header.append(str(key))
        rows = [{name: str(item.get(name, "")) for name in header} for item in payload]
        return header, rows
    if any(isinstance(item, (dict, list)) for item in payload):
        raise VeriError("JSON listesi nesne veya skaler karisimi olamaz.")
    header = ["value"]
    return header, [{"value": str(item)} for item in payload]


def _number(value: str) -> float | None:
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _stats(name: str, values: list[str]) -> dict:
    non_null = [value for value in values if str(value).strip()]
    numbers = [_number(value) for value in non_null]
    numeric = bool(non_null) and all(number is not None for number in numbers)
    examples: list[str] = []
    for value in non_null:
        if value not in examples:
            examples.append(value)
        if len(examples) >= SAMPLE_VALUES:
            break
    stats = {
        "name": name,
        "kind": "number" if numeric else ("empty" if not non_null else "text"),
        "non_null": len(non_null),
        "nulls": len(values) - len(non_null),
        "distinct": len(set(non_null)),
        "examples": examples,
    }
    if numeric:
        clean = [number for number in numbers if number is not None]
        stats.update({
            "min": min(clean),
            "max": max(clean),
            "sum": sum(clean),
            "mean": round(sum(clean) / len(clean), 6),
        })
    return stats


def load_rows(path: Path) -> tuple[str, list[str], list[dict]]:
    """Load one dataset. A live warehouse connector replaces this function."""
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".tsv", ".json"}:
        raise VeriError(f"Desteklenmeyen format: '{suffix or '(yok)'}'.")
    data = _load_bytes(path)
    if len(data) > MAX_READ_BYTES:
        raise VeriError("Kaynak okuma sinirindan buyuk; daha kucuk bir dosya secin.")
    if suffix == ".json":
        header, rows = _parse_json(data)
        return "json", header, rows
    delimiter = "\t" if suffix == ".tsv" else ","
    header, rows = _parse_csv(data, delimiter)
    return ("tsv" if suffix == ".tsv" else "csv"), header, rows


def read_dataset(workspace, path: str, *, max_rows: int = MAX_ROWS) -> dict:
    root = _root(workspace)
    relative = str(path or "").strip()
    if not relative:
        raise VeriError("Okunacak veri dosyasi gerekli.")
    target = _resolve(root, relative)
    if target.is_dir():
        raise VeriError("Bir veri dosyasi secin; dizin okunamaz.")
    if not target.is_file():
        raise VeriError(f"Dosya bulunamadi: {relative}")
    data = _load_bytes(target)
    dataset_format, header, all_rows = load_rows(target)
    truncated = len(all_rows) > max_rows
    rows = all_rows[:max_rows]
    columns = [_stats(name, [row.get(name, "") for row in rows]) for name in header]
    numeric = [column["name"] for column in columns if column["kind"] == "number"]
    return {
        "bridge": BRIDGE_ID,
        "action": "read",
        "level": "read",
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "format": dataset_format,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "row_count": len(all_rows),
        "column_count": len(header),
        "columns": columns,
        "column_names": list(header),
        "rows": rows,
        "preview": rows[:PREVIEW_ROWS],
        "truncated": truncated,
        "numeric_columns": numeric,
        "summary": _three_line(
            [f"{len(all_rows)} satir, {len(header)} kolon ({dataset_format})",
             "Sayisal kolonlar: " + (", ".join(numeric) if numeric else "yok"),
             "sha256 " + hashlib.sha256(data).hexdigest()[:16]],
            "Bos veri kumesi."),
    }


def _analysis(dataset: dict, *, group_by: str = "", metric: str = "count",
              value_column: str = "") -> dict:
    rows = dataset["rows"]
    group_by = str(group_by or "").strip()
    metric = str(metric or "count").strip().lower()
    value_column = str(value_column or "").strip()
    if not group_by:
        return {"kind": "profile", "metric": "count", "groups": [],
                "numeric_columns": dataset["numeric_columns"]}
    if group_by not in dataset["column_names"]:
        raise VeriError(f"Gruplama kolonu bulunamadi: '{group_by}'.")
    if metric not in {"count", "distinct", "sum", "mean", "min", "max"}:
        raise VeriError(f"Desteklenmeyen metrik: '{metric}'.")
    if metric != "count" and not value_column:
        raise VeriError("Sayisal metrik icin 'value_column' gerekli.")
    if metric != "count" and value_column not in dataset["column_names"]:
        raise VeriError(f"Deger kolonu bulunamadi: '{value_column}'.")
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(str(row.get(group_by, "")), []).append(row)
    groups = []
    for key in sorted(buckets):
        items = buckets[key]
        entry = {"group": key, "count": len(items)}
        if metric == "distinct":
            entry["value"] = len({str(item.get(value_column, "")) for item in items})
        elif metric != "count":
            numbers = [_number(item.get(value_column, "")) for item in items]
            clean = [number for number in numbers if number is not None]
            if not clean:
                entry["value"] = None
            elif metric == "sum":
                entry["value"] = round(sum(clean), 6)
            elif metric == "mean":
                entry["value"] = round(sum(clean) / len(clean), 6)
            elif metric == "min":
                entry["value"] = min(clean)
            else:
                entry["value"] = max(clean)
        groups.append(entry)
    return {"kind": "group", "group_by": group_by, "metric": metric,
            "value_column": value_column, "groups": groups}


def _markdown(title: str, dataset: dict, analysis: dict, generated_at: str) -> str:
    lines = [f"# {title}", "",
             f"_Uretildi: {generated_at} - kaynak: veri koprusu_", "",
             "## Kaynak", "",
             f"- Dosya: `{dataset['path']}`",
             f"- Format: {dataset['format']}",
             f"- Satir: {dataset['row_count']} - Kolon: {dataset['column_count']}",
             f"- sha256: `{dataset['sha256']}`", "",
             "## Sema", "",
             "| Kolon | Tur | Dolu | Bos | Tekil | Min | Max | Ortalama |",
             "|---|---|---|---|---|---|---|---|"]
    for column in dataset["columns"]:
        lines.append(
            "| {name} | {kind} | {non_null} | {nulls} | {distinct} | {min} | {max} | {mean} |"
            .format(name=column["name"], kind=column["kind"],
                    non_null=column["non_null"], nulls=column["nulls"],
                    distinct=column["distinct"],
                    min=column.get("min", "-"), max=column.get("max", "-"),
                    mean=column.get("mean", "-")))
    lines.extend(["", "## Analiz", ""])
    if analysis["kind"] == "group":
        lines.extend([f"Gruplama: `{analysis['group_by']}` - metrik: "
                      f"`{analysis['metric']}` - deger: "
                      f"`{analysis['value_column'] or '-'}`", "",
                      "| Grup | Satir | Deger |", "|---|---|---|"])
        for item in analysis["groups"]:
            lines.append(f"| {item['group']} | {item['count']} | "
                         f"{item.get('value', '-')} |")
    else:
        numeric = analysis["numeric_columns"]
        lines.append("Sayisal kolonlar: " + (", ".join(numeric) if numeric else "yok"))
    lines.extend(["", "## Ilk Satirlar", ""])
    if dataset["preview"]:
        header = dataset["column_names"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "---|" * len(header))
        for row in dataset["preview"]:
            lines.append("| " + " | ".join(str(row.get(name, ""))
                                           for name in header) + " |")
    else:
        lines.append("_Satir yok._")
    return "\n".join(lines).rstrip() + "\n"


def _schema_csv(dataset: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["kolon", "tur", "dolu", "bos", "tekil",
                     "min", "max", "ortalama"])
    for column in dataset["columns"]:
        writer.writerow([column["name"], column["kind"], column["non_null"],
                         column["nulls"], column["distinct"],
                         column.get("min", ""), column.get("max", ""),
                         column.get("mean", "")])
    return buffer.getvalue()


def _groups_csv(analysis: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["grup", "satir", "deger"])
    for item in analysis["groups"]:
        writer.writerow([item["group"], item["count"], item.get("value", "")])
    return buffer.getvalue()


def _slug(value: str) -> str:
    folded = str(value or "").casefold()
    folded = (folded.replace("ı", "i").replace("ş", "s").replace("ğ", "g")
              .replace("ü", "u").replace("ö", "o").replace("ç", "c"))
    cleaned = "".join(char if char.isalnum() else "-" for char in folded)
    return "-".join(part for part in cleaned.split("-") if part)[:60] or "veri-raporu"


def build_draft(request: dict) -> dict:
    """Build an analysis report in memory. Nothing is written here."""
    if not isinstance(request, dict):
        raise VeriError("Taslak istegi bir nesne olmali.")
    path = str(request.get("path") or "").strip()
    if not path:
        raise VeriError("Taslak icin 'path' gerekli.")
    workspace = request.get("workspace")
    dataset = read_dataset(workspace, path)
    title = str(request.get("title") or "").strip() or f"{dataset['path']} analizi"
    slug = str(request.get("slug") or "").strip() or _slug(title)
    analysis = _analysis(dataset,
                         group_by=request.get("group_by", ""),
                         metric=request.get("metric", "count"),
                         value_column=request.get("value_column", ""))
    generated_at = str(request.get("generated_at") or
                       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    files = {
        f"{slug}.md": _markdown(title, dataset, analysis, generated_at).encode("utf-8"),
        f"{slug}-ozet.csv": _schema_csv(dataset).encode("utf-8"),
    }
    if analysis["groups"]:
        files[f"{slug}-gruplar.csv"] = _groups_csv(analysis).encode("utf-8")
    return {
        "bridge": BRIDGE_ID,
        "action": "draft",
        "level": "draft",
        "title": title,
        "dataset": {
            "path": dataset["path"],
            "format": dataset["format"],
            "sha256": dataset["sha256"],
            "row_count": dataset["row_count"],
            "column_count": dataset["column_count"],
            "columns": dataset["column_names"],
        },
        "analysis": analysis,
        "files": files,
        "hashes": {name: hashlib.sha256(data).hexdigest()
                   for name, data in files.items()},
        "summary": _three_line(
            [title,
             f"{len(files)} dosya: " + ", ".join(sorted(files)),
             f"Kaynak {dataset['row_count']} satir / {dataset['column_count']} kolon"],
            "Bos taslak."),
        "generated_at": generated_at,
    }


def apply_report(workspace, target: str, files: dict, *,
                 overwrite: bool = False, meta: dict | None = None) -> dict:
    """Write the approved draft files under ``target`` and return a report."""
    root = _root(workspace)
    if not isinstance(files, dict) or not files:
        raise VeriError("Yazilacak rapor dosyasi yok.")
    folder = _resolve(root, target)
    if folder.exists() and not folder.is_dir():
        raise VeriError("Hedef bir dosya; klasor bekleniyordu.")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExternalVeriError(f"Klasor olusturulamadi: {exc}") from exc

    planned: list[tuple[Path, bytes, str]] = []
    for name, payload in files.items():
        relative = Path(str(name))
        if relative.is_absolute() or ".." in relative.parts:
            raise VeriError(f"Guvenli olmayan dosya adi: {name}")
        destination = (folder / relative).resolve()
        if not _inside(root, destination):
            raise VeriError("Hedef calisma alani disinda; yazma reddedildi.")
        if destination.exists() and not overwrite:
            raise VeriError(f"Hedef zaten var; uzerine yazma reddedildi: {name}")
        data = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        planned.append((destination, data, name))

    created = []
    for destination, data, name in planned:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        except OSError as exc:
            raise ExternalVeriError(f"Dosya yazilamadi: {name}: {exc}") from exc
        created.append({
            "path": str(destination.relative_to(root)).replace("\\", "/"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        })
    created.sort(key=lambda item: item["path"])

    report_path = next((item["path"] for item in created
                        if item["path"].endswith(".md")), created[0]["path"])
    try:
        preview = (root / report_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExternalVeriError(f"Rapor onizlemesi okunamadi: {exc}") from exc
    lines = [line for line in preview.splitlines() if line.strip()]
    details = dict(meta or {})
    return {
        "bridge": BRIDGE_ID,
        "action": "apply",
        "level": "apply",
        "target": str(folder.relative_to(root)).replace("\\", "/"),
        "files": created,
        "report_path": report_path,
        "report_preview": preview[:2000],
        "rows": details.get("row_count", 0),
        "columns": details.get("columns", []),
        "summary": details.get("summary") or _three_line(lines, "Rapor yazildi."),
    }


def build_evidence(applied: dict) -> dict:
    """The single evidence shape the delivery record and developer screen read."""
    files = list(applied.get("files") or [])
    report = {
        "path": applied.get("report_path", ""),
        "preview": applied.get("report_preview", "")[:1200],
        "rows": applied.get("rows", 0),
        "columns": list(applied.get("columns") or []),
    }
    return {
        "format": EVIDENCE_FORMAT,
        "bridge": BRIDGE_ID,
        "level": "apply",
        "ok": bool(files),
        "files": [{"path": item["path"], "sha256": item["sha256"],
                   "bytes": item["bytes"]} for item in files],
        "sha256": {item["path"]: item["sha256"] for item in files},
        "summary": applied.get("summary", ""),
        "report": report,
        "target": applied.get("target", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def configure_budget(ledger, profile: dict | None = None) -> dict:
    """Apply the pack's default budget profile to an OperationLedger."""
    budget = dict(profile or DEFAULT_BUDGET)
    scope = str(budget.pop("scope", "bridge:veri"))
    budget.pop("profile", None)
    return await ledger.configure_budget(
        scope,
        token_limit=int(budget.get("token_limit", 0)),
        seconds_limit=int(budget.get("seconds_limit", 0)),
        repairs_limit=int(budget.get("repairs_limit", 0)))


class VeriAdapter:
    """Adapter loaded lazily by the bridge registry (``bridge_packs.veri.adapter:adapter``)."""

    bridge_id = BRIDGE_ID
    levels = ("read", "draft", "apply")

    def read(self, workspace, *, path: str = "", **options) -> dict:
        return read_dataset(workspace, path, **options)

    def draft(self, request: dict) -> dict:
        return build_draft(request)

    def apply(self, workspace, target: str, files, *, overwrite: bool = False,
              meta: dict | None = None) -> dict:
        return apply_report(workspace, target, files, overwrite=overwrite, meta=meta)

    def evidence(self, applied: dict) -> dict:
        return build_evidence(applied)


def adapter() -> VeriAdapter:
    return VeriAdapter()


def manifest() -> dict:
    path = Path(__file__).resolve().parent / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def register(registry, *, replace: bool = True) -> dict:
    """Register (or refresh) the veri pack in a BridgeRegistry."""
    return registry.register(manifest(), replace=replace)
