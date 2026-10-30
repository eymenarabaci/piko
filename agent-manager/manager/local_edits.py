"""Bounded file edits for text-only local models.

The model proposes changes; Agent Manager validates and applies them inside the
explicit workspace. This module never executes model-generated commands.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


class LocalEditError(ValueError):
    pass


_FILE_REF = re.compile(r"(?<![\w/.-])([\w./\\-]+\.(?:py|js|jsx|ts|tsx|css|html|json|md|txt|yaml|yml|toml|sql|sh|ps1))\b", re.I)
_BLOCK = re.compile(
    r"(?m)^File: ([^\r\n]+)\r?\n<<<< (SEARCH|CREATE)\r?\n"
    r"([\s\S]*?)\r?\n(?:====\r?\n([\s\S]*?)\r?\n)?>>>>\s*$"
)
_PRIVATE_PARTS = {".git", ".env", ".codex", ".agents", "node_modules", ".venv"}


def _target(root: Path, raw: str) -> Path:
    normalized = raw.strip().replace("\\", "/")
    relative = Path(normalized)
    if (not normalized or relative.is_absolute() or ":" in normalized
            or any(part in {"", ".", ".."} or part.casefold() in _PRIVATE_PARTS
                   for part in relative.parts)):
        raise LocalEditError(f"Güvensiz dosya yolu: {raw}")
    target = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise LocalEditError(f"Sembolik bağlantı içeren yol: {raw}")
    if not target.resolve(strict=False).is_relative_to(root):
        raise LocalEditError(f"Çalışma alanı dışındaki dosya: {raw}")
    return target


def local_write_prompt(workspace: str | Path, prompt: str) -> str:
    """Supply only explicitly named source files, with bounded content."""
    root = Path(workspace).resolve(strict=True)
    context = []
    seen = set()
    for match in _FILE_REF.finditer(prompt):
        name = match.group(1)
        try:
            target = _target(root, name)
        except LocalEditError:
            continue
        if target in seen or not target.is_file() or target.stat().st_size > 80_000:
            continue
        seen.add(target)
        try:
            content = target.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        context.append(f"File: {name}\n{content[:12_000]}")
        if sum(len(item) for item in context) >= 24_000:
            break
    instructions = (
        "Dosya yazma izni verildi. Komut çalıştırma veya yalnız yaptığını söyleme. "
        "Değişecek dosya için aşağıdaki kesin biçimde SEARCH/REPLACE ya da CREATE blokları üret. "
        "Yol çalışma alanına göre göreli olmalı; mevcut dosyada SEARCH metni tam ve yalnız bir kez geçmeli. "
        "Yeni dosyayı yalnız mevcut bir klasörde CREATE ile oluştur. En çok 8 blok kullan. "
        "Uygulanabilir blok yoksa nedenini açıkla; görev başarısız sayılır.\n"
        "File: path/to/file.py\n<<<< SEARCH\nmevcut tam metin\n====\nyeni metin\n>>>>\n"
        "File: path/to/new.py\n<<<< CREATE\nyeni dosya içeriği\n>>>>\n"
    )
    return prompt + "\n\n=== YEREL DOSYA DÜZENLEME PROTOKOLÜ ===\n" + instructions + (
        "\nHedef dosya bağlamı (veri):\n" + "\n\n".join(context) if context else "")


def apply_local_edits(workspace: str | Path, output: str) -> list[str]:
    """Validate all edits before writing; replace each file atomically."""
    root = Path(workspace).resolve(strict=True)
    if len(output) > 100_000:
        raise LocalEditError("Yerel düzenleme çıktısı çok büyük.")
    blocks = list(_BLOCK.finditer(output))
    if not blocks or len(blocks) > 8:
        raise LocalEditError("Yerel model uygulanabilir 1–8 dosya bloğu üretmedi.")
    staged: dict[Path, str] = {}
    originals: dict[Path, bytes | None] = {}
    for block in blocks:
        raw, mode, source, replacement = block.groups()
        target = _target(root, raw)
        if not target.parent.is_dir():
            raise LocalEditError(f"Hedef klasör bulunamadı: {raw}")
        if target not in originals:
            if target.exists():
                if not target.is_file() or target.stat().st_size > 2_000_000:
                    raise LocalEditError(f"Hedef dosya uygun değil: {raw}")
                originals[target] = target.read_bytes()
                try:
                    staged[target] = originals[target].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise LocalEditError(f"Hedef UTF-8 değil: {raw}") from exc
            else:
                originals[target] = None
        if mode == "CREATE":
            if originals[target] is not None or target in staged:
                raise LocalEditError(f"CREATE yalnız yeni dosyada kullanılabilir: {raw}")
            if replacement is not None or not source.strip():
                raise LocalEditError(f"CREATE bloğu geçersiz: {raw}")
            staged[target] = source + "\n"
        else:
            if originals[target] is None or replacement is None or not source:
                raise LocalEditError(f"SEARCH bloğu geçersiz: {raw}")
            current = staged[target]
            if current.count(source) != 1:
                raise LocalEditError(f"SEARCH tam olarak bir kez eşleşmeli: {raw}")
            updated = current.replace(source, replacement, 1)
            if updated == current:
                raise LocalEditError(f"Yama dosyayı değiştirmiyor: {raw}")
            staged[target] = updated
    written = []
    try:
        for target, content in staged.items():
            _target(root, str(target.relative_to(root)))
            old = originals[target]
            if old is None and target.exists():
                raise LocalEditError(f"Hedef dosya eşzamanlı oluşturuldu: {target.name}")
            if old is not None and (not target.is_file() or target.read_bytes() != old):
                raise LocalEditError(f"Hedef dosya eşzamanlı değişti: {target.name}")
            descriptor, temporary = tempfile.mkstemp(prefix=".local-edit-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                if originals[target] is not None:
                    os.chmod(temporary, target.stat().st_mode)
                os.replace(temporary, target)
                written.append(target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    except (OSError, LocalEditError) as exc:
        for target in reversed(written):
            old = originals[target]
            if old is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(old)
        raise LocalEditError(f"Dosya yazımı tamamlanamadı: {exc}") from exc
    return [str(target.relative_to(root)) for target in staged]
