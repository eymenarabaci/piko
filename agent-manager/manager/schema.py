from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=16000)
    model_id: str = Field(default="auto", min_length=1, max_length=100)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    persona: str = Field(default="", max_length=50)
    skills: list[str] = Field(default_factory=list, max_length=10)
    mcp_servers: list[str] = Field(default_factory=list, max_length=10)
    awaiting_input: bool = False
    user_response: str = Field(default="", max_length=2000)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[Step] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_dag(self):
        ids = {s.id for s in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("Adım kimlikleri benzersiz olmalı.")
        done = set()
        for s in self.steps:
            if s.id in {"plan", "prompt", "results"}:
                raise ValueError("Adım kimliği plan, prompt veya results olamaz.")
            if len(s.dependencies) != len(set(s.dependencies)) or set(s.dependencies) - ids:
                raise ValueError("Geçersiz veya yinelenen bağımlılık.")
        while len(done) < len(ids):
            ready = {s.id for s in self.steps if set(s.dependencies) <= done} - done
            if not ready:
                raise ValueError("Plan döngüsel bağımlılık içeriyor.")
            done |= ready
        return self


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt: str = Field(min_length=1, max_length=16000)
    mode: Literal["plan", "orchestrate", "chat"] = "plan"
    planning_level: Literal["fast", "balanced", "detailed"] = "fast"
    planner_model: str = "gemini-3.8-flash-medium"
    executor_model: str = "auto"
    workspace: str = ""
    project_id: str = ""
    conversation_run_id: str = Field(default="", pattern=r"^(?:[0-9a-f]{32})?$")
    allow_writes: bool = False
    timeout_seconds: int = Field(default=900, ge=10, le=3600)
    workflow_timeout_seconds: int = Field(default=14400, ge=10, le=86400)
    max_parallel: int = Field(default=4, ge=1, le=8)
    execution_backend: Literal["direct", "ao", "cao"] = "direct"


def robust_json_decode(text: str) -> tuple[dict, int]:
    """Decode JSON with resilience against unescaped control characters, trailing commas and prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("Geçerli JSON nesnesi bulunamadı.")
    text = text[start:]

    # 1. Try strict=False decoding (tolerates control characters like unescaped \n and \t)
    try:
        return json.JSONDecoder(strict=False).raw_decode(text)
    except json.JSONDecodeError:
        pass

    # 2. Try removing trailing commas before closing braces/brackets
    sanitized = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.JSONDecoder(strict=False).raw_decode(sanitized)
    except json.JSONDecodeError:
        pass

    # 3. Scan matching curly braces to isolate single valid object
    depth = 0
    candidate_end = -1
    in_str = False
    escape = False
    for i, char in enumerate(text):
        if char == '"' and not escape:
            in_str = not in_str
        elif not in_str:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    candidate_end = i + 1
                    break
        escape = (char == '\\' and not escape)

    if candidate_end > 0:
        chunk = text[:candidate_end]
        chunk = re.sub(r",\s*([}\]])", r"\1", chunk)
        try:
            return json.loads(chunk, strict=False), candidate_end
        except Exception:
            pass

    return json.JSONDecoder(strict=False).raw_decode(text)


def parse_plan(text: str) -> Plan:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        start = text.find("{")
        if start < 0:
            raise ValueError("Planlayıcı JSON planı döndürmedi.")
        text = text[start:]
    value, end = json.JSONDecoder(strict=False).raw_decode(text)
    if text[end:].strip():
        raise ValueError("Planın sonunda beklenmeyen metin var.")
    if isinstance(value, dict) and isinstance(value.get("steps"), list):
        ids = [s.get("id") for s in value["steps"] if isinstance(s, dict) and s.get("id")]
        valid_ids = set(ids)
        for s in value["steps"]:
            if isinstance(s, dict) and "dependencies" in s and isinstance(s["dependencies"], list):
                clean_deps = [d for d in s["dependencies"] if d in valid_ids and d != s.get("id")]
                s["dependencies"] = list(dict.fromkeys(clean_deps))
    return Plan.model_validate(value)


def validate_models(plan: Plan, models: dict):
    for step in plan.steps:
        if step.model_id == "auto":
            continue
        if step.model_id not in models or not models[step.model_id].get("execution_supported", True):
            raise ValueError(f"Çalıştırılamayan model: {step.model_id}")


def workspace_path(value: str, default: Path) -> str:
    path = Path(value).expanduser() if value.strip() else default
    path = path.resolve()
    if not path.is_dir():
        raise ValueError("Çalışma klasörü mevcut bir dizin olmalı.")
    return str(path)
