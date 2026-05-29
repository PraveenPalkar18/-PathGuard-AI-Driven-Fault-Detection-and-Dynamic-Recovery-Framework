#!/usr/bin/env python3
"""
PathGuard: Runtime State Bridge
--------------------------------
Single authoritative state file shared between the monitor (writer)
and the dashboard (reader).  Eliminates the split-brain problem where
both components independently derive severity from different data.

The monitor writes `results/runtime_state.json` atomically after every
telemetry round.  The dashboard reads it on each /api/status poll.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_FILE = Path(__file__).resolve().parent.parent / "results" / "runtime_state.json"


def write_runtime_state(
    *,
    ai_status: str = "NORMAL",
    health_score: int = 100,
    health_label: str = "Healthy",
    confidence: float = 100.0,
    explanation: str = "",
    packet_loss_pct: float = 0.0,
    rtt_avg_ms: float = 0.0,
    recovery_status: str = "NORMAL",
    links: Optional[Dict[str, str]] = None,
    link_metrics: Optional[Dict[str, Dict[str, Any]]] = None,
    failed_links: Optional[List[str]] = None,
    degraded_links: Optional[List[str]] = None,
    recovery_path_links: Optional[List[str]] = None,
    active_recovery_path: str = "None",
    round_number: int = 0,
) -> None:
    """Write the authoritative runtime state atomically to disk.

    Uses write-to-temp + rename for crash safety so the dashboard
    never reads a partially-written file.
    """
    state = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ai_status": ai_status,
        "health_score": health_score,
        "health_label": health_label,
        "confidence": round(confidence, 1),
        "explanation": explanation,
        "packet_loss_pct": round(packet_loss_pct, 2),
        "rtt_avg_ms": round(rtt_avg_ms, 2),
        "recovery_status": recovery_status,
        "links": links or {},
        "link_metrics": link_metrics or {},
        "failed_links": failed_links or [],
        "degraded_links": degraded_links or [],
        "recovery_path_links": recovery_path_links or [],
        "active_recovery_path": active_recovery_path,
        "round_number": round_number,
    }

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file then rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(STATE_FILE.parent), suffix=".tmp", prefix="state_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        try:
            os.chmod(tmp_path, 0o644)
        except Exception:
            pass
        os.replace(tmp_path, str(STATE_FILE))
        try:
            os.chmod(str(STATE_FILE), 0o644)
        except Exception:
            pass
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_runtime_state() -> Optional[Dict[str, Any]]:
    """Read the latest runtime state written by the monitor.

    Returns None if the file doesn't exist or is unreadable.
    """
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
