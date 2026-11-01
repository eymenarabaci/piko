"""Print installed CLI runtimes and the capabilities their manifests declare.

Read-only: this tool probes PATH and each executable's ``--version``. It never
starts an interactive session or a background service.

Usage (from ``agent-manager``)::

    python tools/runtime_probe.py
    python tools/runtime_probe.py --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manager.cli_runtime import probe_all  # noqa: E402

_COLUMNS = (
    ("RUNTIME", 14),
    ("INSTALLED", 10),
    ("VERSION", 12),
    ("RESUME", 7),
    ("MCP", 5),
    ("NONINT", 7),
    ("EXECUTABLE", 0),
)


def _cell(value) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def render_table(results) -> str:
    lines = []
    header = ""
    for title, width in _COLUMNS:
        header += f"{title:<{width}}" if width else title
    lines.append(header.rstrip())
    lines.append("-" * len(header.rstrip()))
    for result in results:
        caps = result.capabilities
        row = (
            f"{result.runtime_id:<{_COLUMNS[0][1]}}"
            f"{('yes' if result.available else 'no'):<{_COLUMNS[1][1]}}"
            f"{_cell(result.version):<{_COLUMNS[2][1]}}"
            f"{('yes' if caps.resume else 'no'):<{_COLUMNS[3][1]}}"
            f"{('yes' if caps.mcp else 'no'):<{_COLUMNS[4][1]}}"
            f"{('yes' if caps.noninteractive else 'no'):<{_COLUMNS[5][1]}}"
            f"{_cell(result.executable)}"
        )
        lines.append(row.rstrip())
        if result.detail:
            lines.append(f"    not: {result.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Bilinen tum runtime'lar icin JSON bas."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Surum komutu icin saniye cinsinden zaman asimi.",
    )
    args = parser.parse_args(argv)
    results = probe_all(timeout=args.timeout)
    if args.json:
        print(json.dumps([result.public() for result in results], indent=2,
                         ensure_ascii=False))
    else:
        print(render_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
