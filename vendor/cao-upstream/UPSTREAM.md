# CAO upstream snapshot

- Upstream: https://github.com/awslabs/cli-agent-orchestrator
- Version: `2.5.4`
- Commit: `6d66dfde29145729e030db91051c91242c825c28`
- License: Apache-2.0; see `LICENSE` and `NOTICE` where applicable.
- Snapshot date: 2026-10-26
- Included upstream paths: `src/`, `pyproject.toml`, `uv.lock`, `README.md`, and `LICENSE`.
- Local modifications: native-Windows CAO engine used by Agent Manager.

Downstream changes add a ConPTY backend (`pywinpty` 3.1.0), Windows file locks,
reparse-point-aware secure opens, output-stream/status bridging, WebSocket PTY
I/O, and native `.exe` launchers for Codex, Antigravity and OpenCode. OpenCode
2.1.4 uses its `mini` subcommand and a separate completion parser. The local
Agent Manager owns the CAO process, port, token and data directory; no external
CAO desktop process is required. Native Codex (`read-only` sandbox) and
Antigravity (`plan` mode) `run-step` calls completed real prompts. OpenCode
launched and returned its actual free
model quota error through `run-step`; a successful paid/authenticated OpenCode
answer has not yet been verified. The Agent Manager settings UI was checked in
headless Chrome with no console or network errors.

This is a focused native port for the three CLI providers above, not a claim
that all upstream CAO features support Windows. Vault operations that require
POSIX handle-relative `dir_fd` traversal fail closed on Windows. Other upstream
providers retain POSIX-specific paths and are outside this integration's
supported list. The upstream Rust TUI was not included in the source snapshot;
Agent Manager supplies the Windows UI. A downstream CAO API route also runs
local Ollama text jobs through the loopback `/api/chat` endpoint with CAO bearer
authentication, job cancellation, timeout, and token counts. It uses `httpx`
0.28.1 and does not provide file tools; writable steps still require Codex or
Antigravity. Keep local changes isolated here so a future CAO update can be
compared with the pinned commit.
