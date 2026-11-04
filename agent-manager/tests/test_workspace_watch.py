from manager.workspace_watch import changes, fingerprint, summarize


def test_fingerprint_tracks_source_files_and_skips_noise(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("a", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("b", encoding="utf-8")
    (tmp_path / "debug.log").write_text("c", encoding="utf-8")

    seen = fingerprint(tmp_path)
    assert "src\\app.tsx" in seen or "src/app.tsx" in seen
    assert not any("node_modules" in key for key in seen)
    assert not any(key.endswith(".log") for key in seen)


def test_changes_detects_edit_add_and_delete(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("one", encoding="utf-8")
    before = fingerprint(tmp_path)
    target.write_text("two", encoding="utf-8")
    (tmp_path / "b.txt").write_text("new", encoding="utf-8")

    diff = changes(before, tmp_path)
    assert diff["count"] == 2
    assert diff["added"] and diff["changed"]

    (tmp_path / "a.txt").unlink()
    gone = changes(before, tmp_path)
    assert gone["removed"]
    assert "silinen" in summarize(gone)


def test_empty_before_never_claims_proof(tmp_path):
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    assert changes({}, tmp_path)["count"] == 0
