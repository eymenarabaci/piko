"""Evidence generator for the izleme (monitoring) bridge pack.

Evidence is a point-in-time snapshot: target, reachability status, at least
three latency samples, the observation window, the thresholds in force and the
sha256 of the snapshot itself. Nothing is sent anywhere; this object is the
only output of the observation path.
"""
from __future__ import annotations

BRIDGE_ID = "izleme"
EVIDENCE_FORMAT = "izleme-evidence"


def build_evidence(snapshot=None, **details) -> dict:
    base = {"format": EVIDENCE_FORMAT, "bridge": BRIDGE_ID, "ok": True}
    if snapshot is None:
        base["summary"] = str(details.get("note") or details.get("summary") or "")
        base["details"] = details
        return base
    thresholds = snapshot.get("thresholds") or {}
    base.update({
        "target": str(snapshot.get("target") or ""),
        "status": str(snapshot.get("status") or ""),
        "latency_samples": list(snapshot.get("latency_samples") or []),
        "window_start": str(snapshot.get("window_start") or ""),
        "window_end": str(snapshot.get("window_end") or ""),
        "threshold": dict(thresholds) if isinstance(thresholds, dict) else thresholds,
        "snapshot_sha256": str(snapshot.get("snapshot_sha256") or ""),
        "summary": str(snapshot.get("summary") or ""),
        "details": details,
    })
    return base
