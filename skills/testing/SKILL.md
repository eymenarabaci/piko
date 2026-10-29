---
name: testing
version: 1.3.0
description: Write executable tests that match the actual implementation and requested runner.
---

Write only declared test files. Prefer complete source excerpts already supplied
in context; call `workspace_read` only when a source is missing or truncated.
Import the real implementation and never duplicate its production functions.
Honor the full user requirements, runtime, dependencies and exact test command.
Use meaningful assertions, isolated temporary paths and deterministic offline
tests. Check imports and discovery. Report only executed checks; ask a question
only if essential source information remains unavailable.
