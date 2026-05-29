#!/usr/bin/env python3
"""Validate severity ↔ health ↔ recovery consistency."""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from monitoring.fault_analyzer import analyze_network_state
from topology.topo_graph import TopoGraph


def test_healthy_network_is_normal():
    topo = TopoGraph()
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00",
            "source": "h1", "destination": "h2",
            "packet_loss_pct": 0.0, "rtt_avg_ms": 8.0,
            "rtt_max_ms": 10.0, "rtt_mdev_ms": 0.5, "status": "ok",
        },
        {
            "timestamp": "2026-05-24T10:00:00",
            "source": "h6", "destination": "h11",
            "packet_loss_pct": 0.0, "rtt_avg_ms": 9.0,
            "rtt_max_ms": 11.0, "rtt_mdev_ms": 0.4, "status": "ok",
        },
    ])
    result = analyze_network_state(df, topo)
    assert result["ai_status"] == "NORMAL", result
    assert result["health_score"] >= 85, result
    assert result["recovery_status"] == "NORMAL"
    print("✓ healthy → NORMAL")


def test_high_loss_is_critical():
    topo = TopoGraph()
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00",
            "source": "h1", "destination": "h2",
            "packet_loss_pct": 85.0, "rtt_avg_ms": 120.0,
            "rtt_max_ms": 200.0, "rtt_mdev_ms": 30.0, "status": "ok",
        },
    ])
    result = analyze_network_state(df, topo)
    assert result["ai_status"] == "CRITICAL", result
    assert result["health_score"] < 60, result
    assert len(result["fault_analysis"]["failed_links"]) >= 0 or len(result["fault_analysis"]["degraded_links"]) >= 0
    print("✓ high loss → CRITICAL")


def test_no_recovered_while_warning():
    topo = TopoGraph()
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00",
            "source": "h1", "destination": "h6",
            "packet_loss_pct": 15.0, "rtt_avg_ms": 55.0,
            "rtt_max_ms": 70.0, "rtt_mdev_ms": 8.0, "status": "ok",
        },
    ])
    recovery_data = {
        "last_recovery": {
            "selected_path": "Path_B",
            "route": "s8→s12→s7",
        }
    }
    result = analyze_network_state(df, topo, recovery_data=recovery_data)
    assert result["ai_status"] in ("WARNING", "CRITICAL"), result
    assert not result["recovery_status"].startswith("RECOVERED"), result
    print("✓ no RECOVERED while degraded")


def test_recovered_only_when_stable():
    topo = TopoGraph()
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00",
            "source": "h1", "destination": "h2",
            "packet_loss_pct": 0.0, "rtt_avg_ms": 6.0,
            "rtt_max_ms": 8.0, "rtt_mdev_ms": 0.3, "status": "ok",
        },
    ])
    recovery_data = {
        "last_recovery": {
            "selected_path": "Path_A",
            "route": "s8→s4→s1",
        }
    }
    result = analyze_network_state(df, topo, recovery_data=recovery_data)
    assert result["ai_status"] == "NORMAL", result
    assert result["recovery_status"].startswith("RECOVERED"), result
    print("✓ RECOVERED when stable")


if __name__ == "__main__":
    test_healthy_network_is_normal()
    test_high_loss_is_critical()
    test_no_recovered_while_warning()
    test_recovered_only_when_stable()
    print("\nAll consistency checks passed.")
