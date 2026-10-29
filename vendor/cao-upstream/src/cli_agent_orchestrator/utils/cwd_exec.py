"""Windows MCP cwd wrapper for providers without a cwd configuration field.

The provider starts this module with discrete argv values. The wrapper runs
the actual MCP command in its requested directory with inherited stdio; a Job
Object owns the child process tree while this wrapper is alive.
"""

from __future__ import annotations

import os
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        print("usage: cwd_exec DIRECTORY COMMAND [ARGS...]", file=sys.stderr)
        return 2
    cwd, command, *command_args = args
    if not os.path.isdir(cwd):
        print(f"MCP working directory does not exist: {cwd}", file=sys.stderr)
        return 2
    if os.name != "nt":
        print("cwd_exec is intended for native Windows MCP processes", file=sys.stderr)
        return 2

    # Inheriting the wrapper's stdio preserves the MCP JSON-RPC byte stream.
    # shell=False preserves the command and argument boundaries.
    process = subprocess.Popen([command, *command_args], cwd=cwd, shell=False)
    try:
        from cli_agent_orchestrator.backends.windows_backend import _WindowsJob

        try:
            job = _WindowsJob(process.pid)
        except Exception:
            process.kill()
            process.wait()
            raise
        try:
            return process.wait()
        finally:
            # Kill-on-close also handles wrapper termination by the provider:
            # Windows closes the wrapper's job handle when its process exits.
            job.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
