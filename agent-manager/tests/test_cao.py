"""CAO execution contract at the Piko boundary; no model calls."""

import httpx
import pytest

from manager.cao import CAOClient, CAOError


@pytest.mark.asyncio
async def test_cao_rejects_composer_echo_and_never_reuses_terminal(tmp_path):
    client = CAOClient(tmp_path, tmp_path)
    client._terminal_pool[f"codex:{tmp_path}"] = "stale123"

    async def request(method, path, *, body, timeout):
        assert body["teardown"] is True
        assert "reuse_terminal_id" not in body
        return {"last_message": "Bu görevde dosyaları düze[Pasted Content 3485 chars]", "terminal_id": "new12345"}

    async def event(*_):
        pass

    client.request = request
    with pytest.raises(CAOError, match="promptun yankısını"):
        await client.run({"id": "codex", "provider": "codex"}, "Görevi yap",
                         workspace=str(tmp_path), allow_writes=False, timeout=30, on_event=event)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "provider", "override"),
    [
        ({"id": "codex", "provider": "codex", "base_model": "gpt-6.1-sol"}, "codex", None),
        ({"id": "gpt-6-astra", "provider": "codex", "base_model": "gpt-6-astra"}, "codex", "gpt-6-astra"),
        ({"id": "gemini-3.8-flash-high", "provider": "antigravity", "base_model": "gemini-3.8-flash"}, "antigravity_cli", "gemini-3.8-flash-high"),
    ],
)
async def test_cao_step_maps_configured_cli_to_run_step(tmp_path, model, provider, override):
    client = CAOClient(tmp_path, tmp_path)
    calls, events = [], []

    async def request(method, path, *, body, timeout):
        calls.append((method, path, body, timeout))
        return {"last_message": "İş tamamlandı.", "terminal_id": "abcdef12"}

    async def event(kind, message, data):
        events.append((kind, data))

    client.request = request
    result = await client.run(model, "Görevi yap", workspace=str(tmp_path),
                              allow_writes=False, timeout=30, on_event=event)
    method, path, body, timeout = calls[0]
    assert (method, path, timeout) == ("POST", "/terminals/run-step", 330)
    assert body["timeout"] == 30
    assert body["provider"] == provider
    assert body["agent"] == "reviewer"
    assert body.get("model") == override
    assert body["teardown"] is True
    assert body["working_directory"] == str(tmp_path)
    assert result["cao_terminal_id"] == "abcdef12"
    assert [row[0] for row in events] == ["cao_starting", "cao_completed"]


@pytest.mark.asyncio
async def test_cao_refuses_unsupported_provider_before_request(tmp_path):
    client = CAOClient(tmp_path, tmp_path)

    async def event(*_):
        pass

    with pytest.raises(CAOError, match="desteklenmiyor"):
        await client.run({"id": "local", "provider": "opencode"}, "Görev", workspace=str(tmp_path),
                         allow_writes=True, timeout=30, on_event=event)


@pytest.mark.asyncio
async def test_cao_ollama_uses_local_text_worker(tmp_path):
    client = CAOClient(tmp_path, tmp_path)
    calls = []

    async def request(method, path, *, body, timeout):
        calls.append((method, path, body, timeout))
        return {"last_message": "TAMAM", "terminal_id": None,
                "usage": {"input_tokens": 4, "output_tokens": 2}}

    async def event(*_):
        pass

    client.request = request
    model = {"id": "qwen3.5:4b", "provider": "ollama", "base_model": "Qwen/Qwen3.5-4B"}
    result = await client.run(model, "Görev", workspace=str(tmp_path),
                              allow_writes=False, timeout=30, on_event=event)
    method, path, body, timeout = calls[0]
    assert (method, path, timeout) == ("POST", "/agent-manager/ollama/run-step", 330)
    assert body["timeout"] == 30
    assert body["model"] == "qwen3.5:4b"
    assert body["prompt"] == "Görev"
    assert result["output"] == "TAMAM"
    assert result["usage"] == {"input_tokens": 4, "output_tokens": 2}
    with pytest.raises(CAOError, match="dosya yazma"):
        await client.run(model, "Görev", workspace=str(tmp_path),
                         allow_writes=True, timeout=30, on_event=event)


@pytest.mark.asyncio
async def test_cao_rejects_terminal_transcript_placeholder(tmp_path):
    client = CAOClient(tmp_path, tmp_path)

    async def request(*_, **__):
        return {"last_message": "[NO RESPONSE - agent completed without producing a text response]", "terminal_id": "abcdef12"}

    async def event(*_):
        pass

    client.request = request
    with pytest.raises(CAOError, match="metin yanıtı üretmedi"):
        await client.run({"id": "codex", "provider": "codex"}, "Görev", workspace=str(tmp_path),
                         allow_writes=False, timeout=30, on_event=event)


@pytest.mark.asyncio
async def test_cao_error_recovers_quota_signal_from_uppercase_terminal_id(tmp_path, monkeypatch):
    client = CAOClient(tmp_path, tmp_path)
    client._base = "http://127.0.0.1:1234"
    client._token = "test-token"
    log = client.home / "logs" / "terminal" / "e38bb172.log"
    log.parent.mkdir(parents=True)
    log.write_text("⚠ Individual quota reached. Resets in 149h.\n", encoding="utf-8")

    async def started():
        return None

    class FakeHTTPClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def request(self, method, url, **_):
            return httpx.Response(
                504,
                json={"detail": {"message": "Terminal e38bb172 provider is in ERROR state", "terminal_id": None}},
                request=httpx.Request(method, url),
            )

    client.ensure_started = started
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: FakeHTTPClient())
    with pytest.raises(CAOError, match="quota reached"):
        await client.request("POST", "/terminals/run-step")


@pytest.mark.asyncio
async def test_cao_error_recovers_quota_from_current_scrollback_when_raw_log_tail_is_noisy(tmp_path, monkeypatch):
    client = CAOClient(tmp_path, tmp_path)
    client._base = "http://127.0.0.1:1234"
    client._token = "test-token"
    log_dir = client.home / "logs" / "terminal"
    log_dir.mkdir(parents=True)
    (log_dir / "3d90b8d7.scrollback").write_text(
        "Claude Sonnet 5.5 (Medium)\n⚠ Individual quota reached. Resets in 149h21m49s.\n> ",
        encoding="utf-8",
    )
    (log_dir / "3d90b8d7.log").write_text("old prompt\n" * 250, encoding="utf-8")

    async def started():
        return None

    class FakeHTTPClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def request(self, method, url, **_):
            return httpx.Response(
                502,
                json={"detail": {"message": "terminal 3d90b8d7 reached ERROR status", "terminal_id": "3d90b8d7"}},
                request=httpx.Request(method, url),
            )

    client.ensure_started = started
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: FakeHTTPClient())
    with pytest.raises(CAOError, match="Individual quota reached"):
        await client.request("POST", "/terminals/run-step")
