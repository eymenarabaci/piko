"""The reference "office" bridge pack: local files, reports, zero external risk.

Levels
------
``read``
    Read a text file, search the workspace, or summarize a directory. Never
    leaves the workspace root.
``draft``
    Build an in-memory report (Markdown + CSV, and XLSX only when the optional
    spreadsheet library is installed). Drafting writes nothing to disk.
``apply``
    Write the approved draft into a target folder inside the workspace, then
    return evidence: the file list, each file's sha256, and a three-line
    content summary.

Everything here is deterministic and local. Errors are mapped onto the pack's
error taxonomy (retryable / needs-user / external-down) so the gate and the
delivery record can report them honestly.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

BRIDGE_ID = "office"
EVIDENCE_FORMAT = "office-evidence"
MAX_READ_BYTES = 400_000
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "data", "work",
             ".pytest_cache", ".graphify-venv", "venv", "build", "dist"}
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".html", ".css", ".js", ".py",
                 ".yml", ".yaml", ".toml", ".sql", ".tsv", ".log"}


class OfficeError(ValueError):
    """Base error that also names its taxonomy class."""

    error_class = "needs-user"


class RetryableOfficeError(OfficeError):
    error_class = "retryable"


class ExternalOfficeError(OfficeError):
    error_class = "external-down"


def error_payload(exc: Exception) -> dict:
    error_class = getattr(exc, "error_class", "needs-user")
    return {"bridge": BRIDGE_ID, "error_class": error_class,
            "message": str(exc)[:600]}


def _root(workspace) -> Path:
    if not str(workspace or "").strip():
        raise OfficeError("Çalışma alanı gerekli.")
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise OfficeError("Çalışma alanı mevcut bir dizin olmalı.")
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
        raise OfficeError("Yol çalışma alanı dışında; reddedildi.")
    return target


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _three_line_summary(lines, fallback: str) -> str:
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    if not cleaned:
        return fallback
    return "\n".join(cleaned[:3])


# --------------------------------------------------------------------------- read


def read_file(workspace, path: str, *, max_bytes: int = MAX_READ_BYTES) -> dict:
    root = _root(workspace)
    target = _resolve(root, path)
    if not target.is_file():
        raise OfficeError(f"Dosya bulunamadı: {path}")
    data = target.read_bytes()
    if len(data) > max_bytes:
        raise OfficeError("Dosya okuma sınırından büyük; hedefli bir arama kullanın.")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return {
        "bridge": BRIDGE_ID,
        "action": "read",
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content": text,
        "summary": _three_line_summary(lines, "Boş dosya."),
    }


def search(workspace, query: str, *, limit: int = 40, max_bytes: int = 2_000_000) -> dict:
    root = _root(workspace)
    needle = str(query or "").strip().casefold()
    if not needle:
        raise OfficeError("Arama için bir metin gerekli.")
    limit = max(1, min(int(limit), 200))
    matches, scanned = [], 0
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
        for name in files:
            path = Path(directory) / name
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            scanned += 1
            relative = str(path.relative_to(root)).replace("\\", "/")
            if needle in relative.casefold():
                matches.append({"path": relative, "line": 0, "text": "(dosya adı)"})
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.casefold():
                    matches.append({"path": relative, "line": number,
                                    "text": line.strip()[:240]})
                    break
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    return {"bridge": BRIDGE_ID, "action": "search", "query": query,
            "scanned_files": scanned, "matches": matches[:limit],
            "summary": f"{len(matches[:limit])} eşleşme, {scanned} dosya tarandı."}


def directory_summary(workspace, path: str = "") -> dict:
    root = _root(workspace)
    target = _resolve(root, path)
    if not target.is_dir():
        raise OfficeError(f"Dizin bulunamadı: {path or '.'}")
    by_suffix: dict[str, int] = {}
    total_bytes = 0
    file_count = 0
    largest = []
    for directory, dirs, files in os.walk(target):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
        for name in files:
            file = Path(directory) / name
            try:
                size = file.stat().st_size
            except OSError:
                continue
            file_count += 1
            total_bytes += size
            suffix = file.suffix.lower() or "(yok)"
            by_suffix[suffix] = by_suffix.get(suffix, 0) + 1
            largest.append((size, str(file.relative_to(root)).replace("\\", "/")))
    largest.sort(reverse=True)
    return {
        "bridge": BRIDGE_ID,
        "action": "summary",
        "path": str(target.relative_to(root)).replace("\\", "/") or ".",
        "file_count": file_count,
        "total_bytes": total_bytes,
        "by_suffix": dict(sorted(by_suffix.items(), key=lambda item: -item[1])),
        "largest": [{"path": path, "bytes": size} for size, path in largest[:5]],
        "summary": (f"{file_count} dosya, {total_bytes} bayt.\n"
                    f"Baskın tür: {max(by_suffix, key=by_suffix.get) if by_suffix else '-'}.\n"
                    f"En büyük: {largest[0][1] if largest else '-'}"),
    }


# -------------------------------------------------------------------------- draft


def _markdown(title: str, sections, generated_at: str) -> str:
    lines = [f"# {title}", "", f"_Üretildi: {generated_at} · kaynak: office köprüsü_", ""]
    for section in sections:
        heading = str(section.get("heading") or "Bölüm").strip()
        body = str(section.get("body") or "").strip()
        bullets = [str(item).strip() for item in section.get("bullets") or [] if str(item).strip()]
        lines.append(f"## {heading}")
        lines.append("")
        if body:
            lines.extend(body.splitlines())
            lines.append("")
        for bullet in bullets:
            lines.append(f"- {bullet}")
        if bullets:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _csv_text(headers, rows) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if headers:
        writer.writerow([str(item) for item in headers])
    for row in rows:
        writer.writerow([str(cell) for cell in row])
    return buffer.getvalue()


def _xlsx_bytes(headers, rows, sheet_name: str = "Rapor") -> bytes | None:
    """Return XLSX bytes only when the optional library is installed."""
    try:
        from openpyxl import Workbook  # type: ignore
    except Exception:
        return None
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = str(sheet_name)[:31] or "Rapor"
    if headers:
        sheet.append([str(item) for item in headers])
    for row in rows:
        sheet.append([str(cell) for cell in row])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_draft(request: dict) -> dict:
    """Build report files in memory. Nothing is written here."""
    if not isinstance(request, dict):
        raise OfficeError("Taslak isteği bir nesne olmalı.")
    title = str(request.get("title") or "").strip()
    if not title:
        raise OfficeError("Taslak için başlık gerekli.")
    sections = request.get("sections") or []
    if not isinstance(sections, list):
        raise OfficeError("'sections' bir liste olmalı.")
    rows = request.get("rows") or []
    if not isinstance(rows, list):
        raise OfficeError("'rows' bir liste olmalı.")
    headers = request.get("table_headers") or []
    slug = str(request.get("slug") or "").strip() or _slug(title)
    generated_at = str(request.get("generated_at") or
                       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    wanted = [str(item).strip().lower()
              for item in (request.get("formats") or ["md", "csv", "xlsx"])]

    files: dict[str, str] = {}
    notes: list[str] = []
    if "md" in wanted or not wanted:
        files[f"{slug}.md"] = _markdown(title, sections, generated_at)
    if rows and ("csv" in wanted or "md" not in wanted):
        files[f"{slug}.csv"] = _csv_text(headers, rows)
    if "xlsx" in wanted and rows:
        payload = _xlsx_bytes(headers, rows, sheet_name=title)
        if payload is None:
            notes.append("xlsx kütüphanesi yok; md/csv ile devam edildi.")
        else:
            files[f"{slug}.xlsx"] = payload
    if not files:
        raise OfficeError("Taslakta üretilecek dosya yok; bölüm veya satır ekleyin.")

    rendered = {name: (value if isinstance(value, bytes) else value.encode("utf-8"))
                for name, value in files.items()}
    return {
        "bridge": BRIDGE_ID,
        "action": "draft",
        "title": title,
        "files": rendered,
        "notes": notes,
        "hashes": {name: hashlib.sha256(data).hexdigest() for name, data in rendered.items()},
        "summary": _three_line_summary(
            [title, f"{len(rendered)} dosya: " + ", ".join(sorted(rendered)),
             notes[0] if notes else "Taslak onay bekliyor."],
            "Boş taslak."),
    }


_ASCII_FOLD = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})


def _slug(value: str) -> str:
    folded = str(value or "").translate(_ASCII_FOLD).casefold()
    cleaned = "".join(char if char.isalnum() else "-" for char in folded)
    return "-".join(part for part in cleaned.split("-") if part)[:60] or "rapor"


# -------------------------------------------------------------------------- apply


def apply_files(workspace, target: str, files: dict) -> dict:
    """Write the approved draft under ``target`` and return the evidence payload."""
    root = _root(workspace)
    if not isinstance(files, dict) or not files:
        raise OfficeError("Yazılacak dosya yok.")
    folder = _resolve(root, target)
    created = []
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExternalOfficeError(f"Klasör oluşturulamadı: {exc}") from exc
    for name, payload in files.items():
        relative = Path(str(name))
        if relative.is_absolute() or ".." in relative.parts:
            raise OfficeError(f"Güvenli olmayan dosya adı: {name}")
        destination = (folder / relative).resolve()
        if not _inside(root, destination):
            raise OfficeError("Hedef çalışma alanı dışında; yazma reddedildi.")
        data = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        except OSError as exc:
            raise ExternalOfficeError(f"Dosya yazılamadı: {name}: {exc}") from exc
        created.append({"path": str(destination.relative_to(root)).replace("\\", "/"),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data)})
    created.sort(key=lambda item: item["path"])
    preview = ""
    for item in created:
        if item["path"].endswith(".md"):
            preview = (folder / item["path"].split("/")[-1]).read_text(
                encoding="utf-8", errors="replace")
            break
    return {
        "bridge": BRIDGE_ID,
        "action": "apply",
        "target": str(folder.relative_to(root)).replace("\\", "/"),
        "files": created,
        "summary": _three_line_summary(
            preview.splitlines() if preview else
            [f"{len(created)} dosya yazıldı.", created[0]["path"] if created else ""],
            "Dosyalar yazıldı."),
    }


def build_evidence(applied: dict) -> dict:
    """The single evidence shape the delivery record and developer screen read."""
    files = list(applied.get("files") or [])
    return {
        "format": EVIDENCE_FORMAT,
        "bridge": BRIDGE_ID,
        "ok": bool(files),
        "files": [{"path": item["path"], "sha256": item["sha256"],
                   "bytes": item["bytes"]} for item in files],
        "sha256": {item["path"]: item["sha256"] for item in files},
        "summary": applied.get("summary", ""),
        "target": applied.get("target", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


class OfficeAdapter:
    """Adapter loaded lazily by the bridge registry (``manager.packs.office:adapter``)."""

    bridge_id = BRIDGE_ID
    levels = ("read", "draft", "apply")

    def read(self, workspace, *, path: str = "", query: str = "") -> dict:
        if query:
            return search(workspace, query)
        if path and _resolve(_root(workspace), path).is_file():
            return read_file(workspace, path)
        return directory_summary(workspace, path)

    def draft(self, request: dict) -> dict:
        return build_draft(request)

    def apply(self, workspace, target: str, files) -> dict:
        return apply_files(workspace, target, files)


def adapter() -> OfficeAdapter:
    return OfficeAdapter()


def manifest() -> dict:
    path = Path(__file__).resolve().parents[2] / "bridge_packs" / BRIDGE_ID / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def register(registry) -> dict:
    """Register (or refresh) the office pack in a BridgeRegistry."""
    return registry.register(manifest(), replace=True)


MANAGER_PERSONA = {"role": "scheduler", "bridges": {BRIDGE_ID: {"levels": ["read", "draft"]}}}
APPROVER_PERSONA = {"role": "implementer", "bridges": {BRIDGE_ID: {"levels": ["read", "draft", "apply"]}}}
