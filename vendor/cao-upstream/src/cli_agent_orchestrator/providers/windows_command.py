"""Launch native Windows CLI providers without passing profile data through cmd.exe.

The terminal's interactive shell is cmd.exe.  POSIX quoting, inline environment
assignments and long system prompts cannot be sent to that shell.  This helper
stores an argv/environment manifest in the current user's temporary directory,
starts a short PowerShell encoded command, consumes the manifest, and then
spawns the native .exe with inherited ConPTY handles.  PowerShell is only a
fixed bootstrap; provider arguments are never interpreted as shell source.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Mapping, Sequence


def build_windows_command(
    executable: str,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    file_arguments: Mapping[int, tuple[str | Path, str]] | None = None,
) -> str:
    """Return a cmd-safe bootstrap for a native .exe and exact argv values.

    ``file_arguments`` maps argument positions to (file, prefix) pairs whose
    full contents replace that argument after the prefix at launch.  This keeps lengthy profile text
    out of cmd.exe's 8191-character input limit.
    """
    if os.name != "nt":
        raise RuntimeError("Windows provider launch is only available on Windows")
    resolved = shutil.which(f"{executable}.exe")
    if executable == "codex":
        packages = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai"
        for bundled in sorted(packages.glob("codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe")):
            if bundled.with_name("codex-code-mode-host.exe").is_file():
                resolved = str(bundled)
                break
    if not resolved and executable == "codex":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            npm_bin = Path(appdata) / "npm" / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
            if npm_bin.is_file():
                resolved = str(npm_bin)
    if not resolved:
        raise FileNotFoundError(
            f"Native {executable}.exe was not found on Windows PATH; "
            "a WSL-only or .cmd installation cannot be used by this launcher"
        )
    if not shutil.which("powershell.exe"):
        raise FileNotFoundError("Windows PowerShell was not found on PATH")

    argv = [resolved, *(str(argument) for argument in arguments)]
    replacements = {
        str(index): {"path": str(path), "prefix": prefix}
        for index, (path, prefix) in (file_arguments or {}).items()
    }
    for index in file_arguments or {}:
        if not isinstance(index, int) or index <= 0 or index >= len(argv):
            raise ValueError("file argument index is outside the command")
    fd, manifest_path = tempfile.mkstemp(prefix="cao-native-launch-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {"argv": argv, "environment": dict(environment or {}), "file_arguments": replacements},
                stream,
                ensure_ascii=False,
            )
        # EncodedCommand is UTF-16LE.  Only its base64 alphabet is parsed by
        # cmd.exe, so '%' and other metacharacters in paths stay literal.
        ps_quote = lambda value: "'" + value.replace("'", "''") + "'"
        command = (
            f"& {ps_quote(sys.executable)} {ps_quote(str(Path(__file__).resolve()))} "
            f"{ps_quote(manifest_path)}; exit $LASTEXITCODE"
        )
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        return f"powershell.exe -NoLogo -NoProfile -EncodedCommand {encoded}"
    except BaseException:
        Path(manifest_path).unlink(missing_ok=True)
        raise


def _run_manifest(path: Path) -> int:
    # Consume and remove before launching the long-lived CLI.  A failed spawn
    # therefore cannot leave profile data in an abandoned manifest.
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)
    argv = manifest["argv"]
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        raise ValueError("invalid Windows launch manifest argv")
    for index_text, replacement in manifest["file_arguments"].items():
        argv[int(index_text)] = replacement["prefix"] + Path(replacement["path"]).read_text(encoding="utf-8")
    environment = os.environ.copy()
    environment.update(manifest["environment"])
    return subprocess.call(argv, env=environment)


if __name__ == "__main__":
    raise SystemExit(_run_manifest(Path(sys.argv[1])))
