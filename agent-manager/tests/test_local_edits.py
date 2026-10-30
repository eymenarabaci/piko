import pytest

from manager.local_edits import LocalEditError, apply_local_edits, local_write_prompt
from manager.providers import Providers
from manager.patcher import PatchBlock, apply_patch_blocks


def test_local_model_can_replace_and_create_inside_workspace(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    output = ("File: app.py\n<<<< SEARCH\nvalue = 1\n====\nvalue = 2\n>>>>\n"
              "File: notes.md\n<<<< CREATE\n# Notes\n>>>>")
    changed = apply_local_edits(tmp_path, output)
    assert set(changed) == {"app.py", "notes.md"}
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "# Notes\n"
    assert "value = 2" in local_write_prompt(tmp_path, "app.py dosyasını değiştir")


@pytest.mark.parametrize("target", ["../escape.py", "C:/escape.py", ".git/config", "nested/../../escape.py"])
def test_local_model_cannot_escape_workspace(tmp_path, target):
    output = f"File: {target}\n<<<< CREATE\nunsafe\n>>>>"
    with pytest.raises(LocalEditError):
        apply_local_edits(tmp_path, output)


def test_local_model_rejects_ambiguous_match_without_writing(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("same\nsame\n", encoding="utf-8")
    with pytest.raises(LocalEditError, match="bir kez"):
        apply_local_edits(tmp_path, "File: app.py\n<<<< SEARCH\nsame\n====\nnew\n>>>>")
    assert target.read_text(encoding="utf-8") == "same\nsame\n"


def test_local_model_rejects_symlink(tmp_path):
    outside = tmp_path.parent / "outside-local-edit.txt"
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink oluşturma yetkisi yok")
    with pytest.raises(LocalEditError, match="Sembolik"):
        apply_local_edits(tmp_path, "File: linked.txt\n<<<< CREATE\nunsafe\n>>>>")
    assert not outside.exists()


def test_local_model_must_change_a_file(tmp_path):
    with pytest.raises(LocalEditError, match="dosya bloğu"):
        apply_local_edits(tmp_path, "İşi yaptım.")


def test_generic_patch_path_is_also_contained(tmp_path):
    count, logs = apply_patch_blocks(tmp_path, [PatchBlock("../outside.txt", "old", "new")])
    assert count == 0
    assert "Güvensiz" in logs[0]


def test_direct_ollama_write_applies_real_patch(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    provider = Providers([{"id": "local", "provider": "ollama", "execution_supported": True}], tmp_path)

    async def fake_ollama(model_id, prompt, timeout, on_event, *, images=None):
        assert "value = 1" in prompt
        return {"output": "File: app.py\n<<<< SEARCH\nvalue = 1\n====\nvalue = 3\n>>>>",
                "model_id": model_id, "status": "completed", "usage": {}}

    async def event(*_):
        return None

    provider._ollama = fake_ollama
    coroutine = provider.run("local", "app.py dosyasını düzelt", workspace=str(tmp_path),
                             allow_writes=True, timeout=30, on_event=event)
    try:
        coroutine.send(None)
    except StopIteration as finished:
        result = finished.value
    else:
        raise AssertionError("Sahte sağlayıcı beklenmeyen asenkron iş başlattı")
    assert result["local_patch_applied"] == ["app.py"]
    assert target.read_text(encoding="utf-8") == "value = 3\n"
