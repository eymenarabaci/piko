import json
from datetime import datetime,timezone
from manager.task_record_audit import task_record_issues

def run_at(tmp_path,data):
    (tmp_path/"STATE.json").write_text(json.dumps(data),encoding="utf-8")
    return {"prompt":"Maintain STATE.json", "workspace":str(tmp_path),"program_items":[{},{}],"program_index":0}

def test_future_and_premature_success_blocked(tmp_path):
    run=run_at(tmp_path,{"meta":{"updated_at":"2099-01-01T00:00:00Z"},"cursor":{"next_action":"Mission accomplished"}})
    issues=task_record_issues(run,current_time=datetime(2026,1,1,tzinfo=timezone.utc))
    assert len(issues)==2

def test_inconsistent_budget_blocked(tmp_path):
    run=run_at(tmp_path,{"budget":{"elapsed_minutes":50,"remaining_minutes":90,"total_minutes":100}})
    assert task_record_issues(run)

def test_current_record_is_not_rewritten(tmp_path):
    run=run_at(tmp_path,{"meta":{"updated_at":"2026-01-01T00:00:00Z"},"budget":{"elapsed_minutes":10,"remaining_minutes":90,"total_minutes":100}})
    original=(tmp_path/"STATE.json").read_bytes()
    assert task_record_issues(run)==[]
    assert (tmp_path/"STATE.json").read_bytes()==original

def test_unrequested_state_not_required(tmp_path):
    assert task_record_issues({"prompt":"write hello","workspace":str(tmp_path)})==[]
