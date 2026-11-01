"""Fail when private material is about to be published to a public repo.

Scans files staged in git (the index also contains every tracked file) and
reports two classes of leaks:

* forbidden file names (private plans, env files, private keys, secrets/tokens)
* absolute local user paths embedded in file contents (comments included)

Exit code 1 lists the offending files, exit code 0 means the tree is clean.
Designed to run from a pre-commit hook but usable standalone.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_NAMES = (
    (re.compile(r"(^|/)piko-yol-haritasi\.md$"), "private roadmap file"),
    (re.compile(r"(^|/)night-parallel-gorevler\.md$"), "private task file"),
    (re.compile(r"(^|/)[^/]*\.env$"), "environment file"),
    (re.compile(r"(^|/)id_rsa[^/]*$"), "private key"),
    (re.compile(r"secret"), "secret in file name"),
    (re.compile(r"token"), "token in file name"),
)

ABSOLUTE_USER_PATH = re.compile(
    r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'<>|]+"
)

TEST_SEGMENTS = {"test", "tests", "fixture", "fixtures", "testing"}
MAX_SCAN_BYTES = 2 * 1024 * 1024


def find_repo_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit("not inside a git repository: " + result.stderr.strip())
    return Path(result.stdout.strip())


def list_index_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit("git ls-files failed: " + result.stderr.decode(errors="replace"))
    return [item for item in result.stdout.decode(errors="replace").split("\0") if item]


def is_test_or_fixture(relpath: str) -> bool:
    parts = relpath.replace("\\", "/").lower().split("/")
    if any(part in TEST_SEGMENTS for part in parts[:-1]):
        return True
    name = parts[-1]
    return (
        name.startswith("test_")
        or name == "conftest.py"
        or ".test." in name
        or ".spec." in name
    )


def check_name(relpath: str) -> str | None:
    lowered = relpath.replace("\\", "/").lower()
    for pattern, reason in FORBIDDEN_NAMES:
        if not pattern.search(lowered):
            continue
        if reason in {"secret in file name", "token in file name"} and is_test_or_fixture(
            relpath
        ):
            continue
        return reason
    return None


def check_content(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_SCAN_BYTES or b"\0" in raw[:8192]:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    match = ABSOLUTE_USER_PATH.search(text)
    if match:
        return "absolute user path: " + match.group(0)
    return None


def scan(root: Path) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for relpath in list_index_files(root):
        reason = check_name(relpath)
        if reason:
            violations.append((relpath, reason))
            continue
        content_reason = check_content(root / relpath)
        if content_reason:
            violations.append((relpath, content_reason))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root (defaults to the current git repository)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve() if args.root else find_repo_root(Path.cwd())
    violations = scan(root)
    if not violations:
        print("public repo check: clean")
        return 0

    print("public repo check: %d violation(s)" % len(violations))
    for relpath, reason in violations:
        print("  %s: %s" % (relpath, reason))
    return 1


if __name__ == "__main__":
    sys.exit(main())
