#!/usr/bin/env python3
"""
PathGuard: Multi-Demo Dashboard System
=======================================
Launches 5 parallel lightweight Flask dashboard instances, each serving
a frozen/live snapshot of one network state.

Ports:
  5001 → NORMAL     (healthy green topology)
  5002 → WARNING    (congested yellow topology)
  5003 → CRITICAL   (failed red link topology)
  5004 → RECOVERING (rerouting orange topology)
  5005 → RECOVERED  (blue bypass path topology)

Architecture:
  • Each server reuses the SAME index.html template and app.js from the main dashboard
  • /api/status returns the frozen snapshot (or latest snapshot if --live-refresh)
  • /api/topology returns the same real topology as port 5000
  • A prominent "📸 CAPTURED FROM REAL EXECUTION" banner is injected via /api/demo-info
  • Port 5000 (real dashboard) is NEVER modified or touched

Snapshot data comes exclusively from:
  results/demo_states/{normal,warning,critical,recovering,recovered}.json
  (written by snapshot_capture.py from the REAL runtime_state.json)

Usage:
  # Single state server:
  python3 dashboard/demo_dashboards.py --state normal --port 5001

  # All 5 servers in one process:
  python3 dashboard/demo_dashboards.py --all

  # All 5 with live snapshot refresh:
  python3 dashboard/demo_dashboards.py --all --live-refresh

  # One-click launcher:
  sudo ./demo/run_multi_dashboard_demo.sh
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from flask import Flask, render_template, jsonify, request

# ── Paths ────────────────────────────────────────────────────────────────────
DEMO_DIR    = project_root / "results" / "demo_states"
EVENTS_LOG  = project_root / "results" / "events.log"
TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR   = Path(__file__).parent / "static"

SNAPSHOT_FILES = {
    "normal":     DEMO_DIR / "normal.json",
    "warning":    DEMO_DIR / "warning.json",
    "critical":   DEMO_DIR / "critical.json",
    "recovering": DEMO_DIR / "recovering.json",
    "recovered":  DEMO_DIR / "recovered.json",
}

PORT_MAP = {
    "normal":     5001,
    "warning":    5002,
    "critical":   5003,
    "recovering": 5004,
    "recovered":  5005,
}

STATE_EMOJI = {
    "normal":     "🟢",
    "warning":    "🟡",
    "critical":   "🔴",
    "recovering": "🟠",
    "recovered":  "🔵",
}

STATE_COLORS = {
    "normal":     "#22c55e",
    "warning":    "#f59e0b",
    "critical":   "#ef4444",
    "recovering": "#f97316",
    "recovered":  "#3b82f6",
}

STATE_TITLES = {
    "normal":     "NORMAL — Healthy Network",
    "warning":    "WARNING — Congested Network",
    "critical":   "CRITICAL — Link Failure",
    "recovering": "RECOVERING — BFS Rerouting",
    "recovered":  "RECOVERED — Bypass Active",
}


# ────────────────────────────────────────────────────────────────────────────
# Topology helper (shared with main dashboard)
# ────────────────────────────────────────────────────────────────────────────
try:
    from topology.topo_graph import TopoGraph
    _topo = TopoGraph()
except Exception:
    _topo = None

def _build_topology_json() -> Dict[str, Any]:
    """Return topology in the same format as the main dashboard's /api/topology."""
    default_nodes, default_links = _default_topology()
    if not _topo:
        return {"nodes": default_nodes, "links": default_links}
    try:
        nodes, links = [], []
        for s_name in _topo.switches:
            layer = "core" if s_name in ["s1","s2","s3"] else ("distribution" if s_name in ["s4","s5","s6","s7"] else "access")
            nodes.append({"id": s_name, "label": s_name.upper(), "type": "switch", "layer": layer})
        for host_name, host_info in _topo.hosts.items():
            nodes.append({"id": host_name, "label": host_name, "type": "host", "layer": "access"})
            sw = host_info.get("switch")
            if sw:
                links.append({"source": host_name, "target": sw, "id": f"{host_name}-{sw}"})
        for link in _topo.get_all_links():
            if isinstance(link, str) and "-" in link:
                u, v = link.split("-", 1)
                links.append({"source": u, "target": v, "id": link})
        if nodes and links:
            return {"nodes": nodes, "links": links}
    except Exception:
        pass
    return {"nodes": default_nodes, "links": default_links}


def _default_topology():
    nodes, links = [], []
    for i in range(1, 4):
        nodes.append({"id": f"s{i}", "label": f"S{i}", "type": "switch", "layer": "core"})
    for i in range(4, 8):
        nodes.append({"id": f"s{i}", "label": f"S{i}", "type": "switch", "layer": "distribution"})
    for i in range(8, 13):
        nodes.append({"id": f"s{i}", "label": f"S{i}", "type": "switch", "layer": "access"})
    for i in range(1, 25):
        sw_idx = 8 + (i - 1) // 5
        nodes.append({"id": f"h{i}", "label": f"h{i}", "type": "host", "layer": "access"})
        links.append({"source": f"h{i}", "target": f"s{sw_idx}", "id": f"h{i}-s{sw_idx}"})
    for u, v in [("s1","s2"),("s1","s3"),("s2","s3"),("s4","s1"),("s4","s2"),
                  ("s5","s1"),("s5","s3"),("s6","s2"),("s6","s3"),("s7","s1"),("s7","s2"),
                  ("s8","s4"),("s8","s5"),("s9","s4"),("s9","s6"),("s10","s5"),("s10","s7"),
                  ("s11","s6"),("s11","s7"),("s12","s6"),("s12","s7")]:
        nu, nv = min(u,v), max(u,v)
        links.append({"source": u, "target": v, "id": f"{nu}-{nv}"})
    return nodes, links


# ────────────────────────────────────────────────────────────────────────────
# Snapshot reader
# ────────────────────────────────────────────────────────────────────────────

def _read_snapshot(state_label: str) -> Optional[Dict[str, Any]]:
    path = SNAPSHOT_FILES[state_label]
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _fallback_state(state_label: str) -> Dict[str, Any]:
    """
    Return a realistic-looking pre-seeded state when no real snapshot exists yet.
    These are reasonable defaults — NOT fake runtime injection.
    They're only used as placeholder until a real snapshot is captured.
    """
    base = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "recovery_path_links": [],
        "failed_links":    [],
        "degraded_links":  [],
        "round_number":    0,
        "chart_data": {
            "labels":  [f"T-{i*3}s" for i in range(20, 0, -1)],
            "latency": [5.0] * 20,
            "loss":    [0.0] * 20,
        },
        "path_rankings": [],
        "timeline": ["Waiting for real snapshot — run the live demo to capture real states"],
        "debug_info": {
            "failed_link": "None",
            "active_recovery_path": "None",
            "recovery_trigger_reason": "Awaiting real snapshot from live demo",
            "selected_path_score": "N/A",
            "controller_action": "Active Monitoring",
            "openflow_status": "STABLE (Full-Mesh)",
            "telemetry_ts": "N/A",
            "last_recovery_ts": "None",
        },
        "capture_proof": {
            "state_label": state_label,
            "captured_at": "NOT YET CAPTURED",
            "source": "Pre-seeded placeholder — run live demo to capture real state",
            "proof_statement": "⚠️  No real snapshot yet. Run: sudo ./demo/run_real_fast_demo.sh",
        },
    }

    if state_label == "normal":
        base.update({
            "ai_status": "NORMAL", "health_score": 100, "health_label": "Healthy",
            "confidence": 99.0,
            "explanation": "Network healthy — 100/100, 0.0% loss, 5.2ms avg RTT",
            "packet_loss_pct": 0.0, "rtt_avg_ms": 5.2, "recovery_status": "NORMAL",
            "links": {k: "up" for k in ["s1-s2","s1-s3","s1-s4","s1-s5","s2-s3","s2-s4",
                                          "s2-s6","s3-s5","s3-s6","s4-s8","s4-s9","s5-s8",
                                          "s5-s10","s6-s9","s6-s11","s6-s12","s7-s10","s7-s11","s7-s12"]},
            "fault_analysis": {"failed_links":[],"degraded_links":[],"root_causes":["All links operational"],"active_issues":[]},
        })
    elif state_label == "warning":
        base.update({
            "ai_status": "WARNING", "health_score": 78, "health_label": "Degraded",
            "confidence": 87.0,
            "explanation": "Degraded conditions on s4-s8 — 35.0ms, 10.0% loss",
            "packet_loss_pct": 10.0, "rtt_avg_ms": 35.0, "recovery_status": "DEGRADED",
            "links": {**{k: "up" for k in ["s1-s2","s1-s3","s1-s4","s1-s5","s2-s3","s2-s4",
                                             "s2-s6","s3-s5","s3-s6","s4-s9","s5-s8","s5-s10",
                                             "s6-s9","s6-s11","s6-s12","s7-s10","s7-s11","s7-s12"]},
                       "s4-s8": "warning"},
            "degraded_links": ["s4-s8"],
            "link_metrics": {"s4-s8": {"loss_pct": 10.0, "latency_ms": 35.0, "status": "warning"}},
            "fault_analysis": {"failed_links":[],"degraded_links":[{"link":"s4-s8","message":"Degraded link s4-s8 — elevated latency/loss"}],"root_causes":["Degraded link s4-s8"],"active_issues":[]},
        })
    elif state_label == "critical":
        base.update({
            "ai_status": "CRITICAL", "health_score": 0, "health_label": "Critical",
            "confidence": 100.0,
            "explanation": "Critical failure on s4-s8 (access layer) — 100% packet loss",
            "packet_loss_pct": 100.0, "rtt_avg_ms": 0.0, "recovery_status": "NORMAL",
            "failed_links": ["s4-s8"],
            "links": {**{k: "up" for k in ["s1-s2","s1-s3","s1-s4","s1-s5","s2-s3","s2-s4",
                                             "s2-s6","s3-s5","s3-s6","s4-s9","s5-s8","s5-s10",
                                             "s6-s9","s6-s11","s6-s12","s7-s10","s7-s11","s7-s12"]},
                       "s4-s8": "down"},
            "fault_analysis": {"failed_links":[{"link":"s4-s8","layer":"access","loss_pct":100.0,"latency_ms":0.0,"status":"down","message":"Link s4-s8 DOWN"}],"degraded_links":[],"root_causes":["Critical failure on s4-s8 (access layer)"],"active_issues":[]},
        })
    elif state_label == "recovering":
        base.update({
            "ai_status": "CRITICAL", "health_score": 0, "health_label": "Critical",
            "confidence": 100.0,
            "explanation": "Initiating dynamic SDN recovery for failed links: s4-s8",
            "packet_loss_pct": 100.0, "rtt_avg_ms": 0.0,
            "recovery_status": "RECOVERING (Path_1: s8 → s5 → s1 → s7 → s12)",
            "failed_links": ["s4-s8"],
            "links": {**{k: "up" for k in ["s1-s2","s1-s3","s1-s4","s1-s5","s2-s3","s2-s4",
                                             "s2-s6","s3-s5","s3-s6","s4-s9","s5-s8","s5-s10",
                                             "s6-s9","s6-s11","s6-s12","s7-s10","s7-s11","s7-s12"]},
                       "s4-s8": "down"},
            "debug_info": {**base["debug_info"],
                           "failed_link": "s4-s8",
                           "active_recovery_path": "Path_1",
                           "recovery_trigger_reason": "100% packet loss on s4-s8 — BFS reroute triggered",
                           "selected_path_score": "89/100",
                           "controller_action": "Rerouting traffic via OpenFlow",
                           "openflow_status": "UPDATING"},
            "fault_analysis": {"failed_links":[{"link":"s4-s8","layer":"access","loss_pct":100.0,"latency_ms":0.0,"status":"down","message":"Link s4-s8 DOWN — recovery in progress"}],"degraded_links":[],"root_causes":["Critical failure on s4-s8 — auto-recovery active"],"active_issues":[]},
        })
    elif state_label == "recovered":
        recovery_path = ["s5-s8","s1-s5","s1-s7","s7-s12"]
        base.update({
            "ai_status": "NORMAL", "health_score": 92, "health_label": "Healthy",
            "confidence": 99.0,
            "explanation": "RECOVERED via Path_1 (s8 → s5 → s1 → s7 → s12) — bypass active",
            "packet_loss_pct": 0.0, "rtt_avg_ms": 8.2,
            "recovery_status": "RECOVERED (Path_1: s8 → s5 → s1 → s7 → s12)",
            "failed_links": ["s4-s8"],
            "recovery_path_links": recovery_path,
            "active_recovery_path": "Path_1",
            "links": {**{k: "up" for k in ["s1-s2","s1-s3","s1-s4","s2-s3","s2-s4",
                                             "s2-s6","s3-s5","s3-s6","s4-s9",
                                             "s6-s9","s6-s11","s6-s12","s7-s10","s7-s11","s7-s12"]},
                       "s4-s8": "down", "s5-s8": "recovery", "s1-s5": "recovery",
                       "s1-s7": "recovery", "s7-s12": "recovery"},
            "debug_info": {**base["debug_info"],
                           "failed_link": "s4-s8",
                           "active_recovery_path": "Path_1",
                           "recovery_trigger_reason": "BFS selected Path_1 (score=89/100)",
                           "selected_path_score": "89/100",
                           "controller_action": "Restoring to full-mesh rules",
                           "openflow_status": "SUCCESS (Rules Active)",
                           "last_recovery_ts": datetime.now(timezone.utc).strftime("%H:%M:%S")},
            "fault_analysis": {"failed_links":[{"link":"s4-s8","layer":"access","loss_pct":100.0,"latency_ms":0.0,"status":"down","message":"Link s4-s8 DOWN (traffic rerouted)"}],"degraded_links":[],"root_causes":["BFS bypass: s8→s5→s1→s7→s12 active — verification ping passed"],"active_issues":[]},
        })
    return base


# ────────────────────────────────────────────────────────────────────────────
# Demo Flask application factory
# ────────────────────────────────────────────────────────────────────────────

def create_demo_app(state_label: str, live_refresh: bool = False) -> Flask:
    """Create a Flask app for one demo state."""
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )

    state_color  = STATE_COLORS[state_label]
    state_emoji  = STATE_EMOJI[state_label]
    state_title  = STATE_TITLES[state_label]
    port         = PORT_MAP[state_label]

    # In-memory chart history accumulator for this server
    _chart_history = {
        "labels":  [],
        "latency": [],
        "loss":    [],
    }
    _last_snapshot_ts = [""]

    def _get_status_data() -> Dict[str, Any]:
        """Load snapshot or fallback. Optionally refresh if live_refresh=True."""
        snapshot = _read_snapshot(state_label)
        if snapshot is None:
            return _fallback_state(state_label)

        # If live_refresh, always re-read the latest snapshot
        data = snapshot

        # Update chart history (accumulate real points over time)
        ts = data.get("timestamp", "")
        ts_short = ts[11:19] if len(ts) > 19 else ts
        if ts_short and ts_short != _chart_history.get("_last", ""):
            if "chart_data" in data:
                _chart_history["labels"]  = data["chart_data"]["labels"]
                _chart_history["latency"] = data["chart_data"]["latency"]
                _chart_history["loss"]    = data["chart_data"]["loss"]
                _chart_history["_last"]   = ts_short

        if _chart_history["labels"]:
            data["chart_data"] = {
                "labels":  _chart_history["labels"],
                "latency": _chart_history["latency"],
                "loss":    _chart_history["loss"],
            }

        return data

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def status():
        data = _get_status_data()
        return jsonify(data)

    @app.route("/api/topology")
    def topology():
        return jsonify(_build_topology_json())

    @app.route("/api/demo-info")
    def demo_info():
        """Banner data for the demo dashboard overlay."""
        snapshot = _read_snapshot(state_label)
        proof = snapshot.get("capture_proof", {}) if snapshot else {}
        return jsonify({
            "state_label":    state_label,
            "state_title":    state_title,
            "state_emoji":    state_emoji,
            "state_color":    state_color,
            "port":           port,
            "live_refresh":   live_refresh,
            "snapshot_exists": snapshot is not None,
            "captured_at":    proof.get("captured_at", "N/A"),
            "proof_statement": proof.get("proof_statement", "Awaiting real snapshot"),
            "ai_model":       proof.get("ai_model", "RandomForest (ai/model.pkl)"),
            "monitor_round":  proof.get("monitor_round", 0),
            "bfs_paths":      proof.get("bfs_paths_computed", 0),
            "real_confidence": proof.get("real_confidence", 0.0),
            "recovery_route": proof.get("recovery_route", "N/A"),
        })

    return app


# ────────────────────────────────────────────────────────────────────────────
# Single-server runner (called in subprocess)
# ────────────────────────────────────────────────────────────────────────────

def run_single_server(state_label: str, port: int, live_refresh: bool = False):
    """Entry point for a single demo server subprocess."""
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)   # suppress Flask request noise

    app = create_demo_app(state_label, live_refresh)
    print(f"  {STATE_EMOJI[state_label]}  Demo server [{state_label.upper():12s}] → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# ────────────────────────────────────────────────────────────────────────────
# Multi-server launcher
# ────────────────────────────────────────────────────────────────────────────

def launch_all_servers(live_refresh: bool = False, states: list = None):
    """Launch all 5 demo servers as parallel subprocesses."""
    if states is None:
        states = list(PORT_MAP.keys())

    print(f"\n  {'─'*62}")
    print(f"  PathGuard Multi-Demo Dashboard System")
    print(f"  {'─'*62}")

    processes = []
    for state_label in states:
        port = PORT_MAP[state_label]
        p = multiprocessing.Process(
            target=run_single_server,
            args=(state_label, port, live_refresh),
            name=f"pg-demo-{state_label}",
            daemon=True,
        )
        p.start()
        processes.append((state_label, port, p))
        time.sleep(0.2)  # stagger startup slightly

    # Wait for all to bind
    import socket
    for state_label, port, p in processes:
        ok = False
        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    ok = True
                    break
            except OSError:
                time.sleep(0.3)
        emoji = STATE_EMOJI[state_label]
        if ok:
            print(f"  {emoji}  http://localhost:{port}  [{state_label.upper():12s}] ✓")
        else:
            print(f"  ⚠  http://localhost:{port}  [{state_label.upper():12s}] (may need a moment)")

    print(f"  {'─'*62}")
    print(f"  Port 5000 = REAL live system (unchanged)")
    print(f"  {'─'*62}\n")

    return processes


# ────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PathGuard Multi-Demo Dashboard Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Launch all 5 demo servers:
  python3 dashboard/demo_dashboards.py --all

  # Launch with live snapshot refresh:
  python3 dashboard/demo_dashboards.py --all --live-refresh

  # Launch single state:
  python3 dashboard/demo_dashboards.py --state critical --port 5003
        """,
    )
    parser.add_argument("--all",          action="store_true", help="Launch all 5 demo servers")
    parser.add_argument("--state",        choices=list(PORT_MAP.keys()), help="Launch single state server")
    parser.add_argument("--port",         type=int, help="Port for single state server")
    parser.add_argument("--live-refresh", action="store_true",
                        help="Re-read snapshots on each /api/status request (live updates)")
    parser.add_argument("--states",       nargs="+", choices=list(PORT_MAP.keys()),
                        help="Launch specific states (subset of all)")
    args = parser.parse_args()

    if args.state:
        port = args.port or PORT_MAP[args.state]
        run_single_server(args.state, port, args.live_refresh)

    elif args.all or args.states:
        states = args.states or list(PORT_MAP.keys())
        processes = launch_all_servers(args.live_refresh, states)

        print("  Press Ctrl+C to stop all servers.\n")
        try:
            while True:
                time.sleep(10)
                alive = sum(1 for _, _, p in processes if p.is_alive())
                if alive < len(processes):
                    print(f"  ⚠  {len(processes) - alive} server(s) stopped unexpectedly")
        except KeyboardInterrupt:
            print("\n  Stopping all demo servers...")
            for _, _, p in processes:
                p.terminate()
            for _, _, p in processes:
                p.join(timeout=3)
    else:
        parser.print_help()


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    main()
