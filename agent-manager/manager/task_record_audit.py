"""Read-only checks of task-owned state; never manufacture progress."""
import json
from pathlib import Path
from datetime import datetime, timezone


def task_record_issues(run: dict, *, current_time=None) -> list[str]:
    if "state.json" not in str(run.get("prompt", "")).lower():
        return []
    root=Path(run.get("workspace") or ".").resolve()
    path=(root/"STATE.json").resolve()
    if not path.is_relative_to(root):
        return ["STATE.json çalışma alanı dışına işaret ediyor"]
    if not path.exists():
        return ["İstenen STATE.json bulunamadı"]
    try:
        if path.stat().st_size>1_000_000:
            return ["STATE.json denetim boyut sınırını aşıyor"]
        data=json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data,dict):
            return ["STATE.json nesne değil"]
    except (OSError,ValueError) as exc:
        return ["STATE.json okunamıyor: "+type(exc).__name__]
    issues=[]
    meta=data.get("meta") or {}
    stamp=meta.get("updated_at") if isinstance(meta,dict) else None
    now=current_time or datetime.now(timezone.utc)
    if stamp:
        try:
            parsed=datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
            if parsed.tzinfo is None:issues.append("STATE updated_at saat dilimi eksik")
            elif (parsed-now).total_seconds()>60:issues.append("STATE updated_at gelecekte")
        except ValueError:issues.append("STATE updated_at geçersiz")
    budget=data.get("budget") or {}
    if isinstance(budget,dict):
        values=[budget.get(k) for k in ("elapsed_minutes","remaining_minutes","total_minutes")]
        if all(isinstance(x,(int,float)) and not isinstance(x,bool) for x in values):
            if abs(values[0]+values[1]-values[2])>1:issues.append("STATE geçen+kalan süre toplam bütçeyle uyuşmuyor")
    cursor=data.get("cursor") or {}
    program=run.get("program_items") or []
    if isinstance(cursor,dict) and int(run.get("program_index",0))<len(program)-1:
        action=str(cursor.get("next_action","")).lower()
        if any(term in action for term in ("mission accomplished","tüm görev tamamlandı","all work completed")):
            issues.append("STATE tüm görevi bitmiş gösteriyor; gerçek programda sonraki dilimler var")
    return issues
