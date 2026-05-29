#!/usr/bin/env python3
"""
PathGuard: Smart Demo Launcher
================================
Selects the appropriate demo mode based on available infrastructure.

DEFAULT (real mode):
    Delegates to demo/real_fast_demo.py which uses:
    • Actual Mininet 12-switch OVS topology
    • Actual RandomForest ML model (ai/model.pkl)
    • Actual NetworkMonitor telemetry pipeline
    • Actual net.configLinkStatus() for link events
    • Actual PathRanker BFS path ranking
    • Actual POX /reroute REST → ofp_flow_mod
    • Actual iperf + ping traffic
    runtime_state.json written ONLY by monitor.py and recovery engine.
    NO FAKE STATE INJECTION.

SIMULATION MODE (--simulate):
    Writes runtime_state.json directly for dashboard-only testing.
    ⚠️  FAKE/SIMULATION — NOT for final exam or real SDN demo.
    Use only when Mininet/POX are unavailable (e.g., CI, dry-runs).

Usage:
    # REAL (recommended — requires sudo + POX + model.pkl):
    sudo python3 demo/fast_demo.py

    # One-click real demo:
    sudo ./demo/run_real_fast_demo.sh

    # Simulation only (no Mininet/POX — dashboard testing only):
    python3 demo/fast_demo.py --simulate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── Project root ───────────────────────────────────────────────────────────
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# ─── ANSI colours ───────────────────────────────────────────────────────────
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
BLUE    = "\033[94m"
CYAN    = "\033[96m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"


# ══════════════════════════════════════════════════════════════════════════════
# Environment checks (pre-flight)
# ══════════════════════════════════════════════════════════════════════════════

def check_model() -> bool:
    model_path = project_root / "ai" / "model.pkl"
    return model_path.exists()


def check_pox_port() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 6633), timeout=1):
            return True
    except OSError:
        return False


def check_root() -> bool:
    return os.geteuid() == 0


def print_preflight_status():
    root_ok  = check_root()
    model_ok = check_model()
    pox_ok   = check_pox_port()

    print(f"\n  {BOLD}Pre-flight checks:{RESET}")
    print(f"    {'✅' if root_ok  else '❌'}  Root privileges  {'(ok)' if root_ok  else '(run with sudo)'}")
    print(f"    {'✅' if model_ok else '⚠️ '}  AI model.pkl     {'(loaded)' if model_ok else f'(missing — run: python3 ai/train_model.py)'}")
    print(f"    {'✅' if pox_ok   else '⚠️ '}  POX :6633        {'(running)' if pox_ok else '(not detected — run: sudo ./controller/run_pox.sh)'}")
    return root_ok, model_ok, pox_ok


# ══════════════════════════════════════════════════════════════════════════════
# REAL MODE — Delegate to real_fast_demo.py
# ══════════════════════════════════════════════════════════════════════════════

def run_real_demo(no_pause: bool = False) -> None:
    """Delegate to the real AI-driven SDN demo."""
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════════════╗
║     🛡️  PathGuard Fast Demo  —  REAL AI-Driven SDN Self-Healing      ║
╠══════════════════════════════════════════════════════════════════════╣
║  Mode:  REAL  (no fake state injection)                              ║
║  ML:    RandomForest (ai/model.pkl)  →  predict_advanced()           ║
║  Net:   Mininet 12-switch OVS topology                               ║
║  OF:    POX controller  →  ofp_flow_mod  →  all 12 switches          ║
║  BFS:   PathRanker.evaluate_paths()  →  excluded failed links        ║
╠══════════════════════════════════════════════════════════════════════╣
║  Dashboard:   http://localhost:5000                                  ║
║  State file:  results/runtime_state.json  (written by monitor only) ║
╚══════════════════════════════════════════════════════════════════════╝{RESET}
""")

    root_ok, model_ok, pox_ok = print_preflight_status()
    print()

    if not root_ok:
        print(f"  {RED}✖  Mininet requires root. Run:{RESET}")
        print(f"     {BOLD}sudo python3 demo/fast_demo.py{RESET}")
        print(f"  Or use the one-click launcher:  {BOLD}sudo ./demo/run_real_fast_demo.sh{RESET}\n")
        sys.exit(1)

    if not model_ok:
        print(f"  {YELLOW}⚠  AI model not found. Train it first:{RESET}")
        print(f"     {BOLD}python3 ai/train_model.py{RESET}")
        print(f"  Continuing — monitor will use heuristic fallback.\n")

    if not pox_ok:
        print(f"  {YELLOW}⚠  POX controller not detected on :6633.{RESET}")
        print(f"     Start it in another terminal:  {BOLD}sudo ./controller/run_pox.sh{RESET}")
        print(f"  Continuing — recovery will still attempt REST call.\n")

    # Delegate to real_fast_demo.py
    real_demo = project_root / "demo" / "real_fast_demo.py"
    print(f"  {GREEN}▶  Launching real demo: {real_demo}{RESET}\n")
    print(f"  {'─' * 68}\n")
    time.sleep(1)

    # Use os.execv so this process IS the real demo (clean process replace)
    os.execv(sys.executable, [sys.executable, str(real_demo)])


# ══════════════════════════════════════════════════════════════════════════════
# SIMULATION MODE — Dashboard-only testing (NO REAL SDN)
# ══════════════════════════════════════════════════════════════════════════════

STATE_FILE   = project_root / "results" / "runtime_state.json"
METRICS_FILE = project_root / "results" / "recovery_metrics.json"
EVENTS_LOG   = project_root / "results" / "events.log"

# Tuneable parameters for simulation mode
PHASE_DURATION_SEC = 8
TICK_INTERVAL      = 1

# Full 12-switch topology link list
ALL_LINKS = [
    "s1-s2", "s1-s3", "s1-s4", "s1-s5", "s1-s7",
    "s2-s3", "s2-s4", "s2-s6", "s2-s7",
    "s3-s5", "s3-s6",
    "s4-s8", "s4-s9",
    "s5-s8", "s5-s10",
    "s6-s9", "s6-s11", "s6-s12",
    "s7-s10", "s7-s11", "s7-s12",
    "s10-s5", "s10-s7",
    "s11-s6", "s11-s7",
    "s12-s6", "s12-s7",
]

RECOVERY_PATH_LINKS = ["s5-s8", "s1-s5", "s1-s6", "s6-s12"]
RECOVERY_ROUTE_STR  = "s8 → s5 → s1 → s6 → s12"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="pg_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        os.replace(tmp, str(path))
        try:
            os.chmod(str(path), 0o644)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_event(msg: str, level: str = "INFO") -> None:
    try:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with open(EVENTS_LOG, "a") as f:
            f.write(f"[{ts}] {level}: {msg}\n")
    except PermissionError:
        pass


def _write_state(
    ai_status: str,
    health_score: int,
    health_label: str,
    confidence: float,
    explanation: str,
    packet_loss_pct: float,
    rtt_avg_ms: float,
    recovery_status: str,
    links: dict,
    link_metrics: dict | None = None,
    failed_links: list | None = None,
    degraded_links: list | None = None,
    recovery_path_links: list | None = None,
    active_recovery_path: str = "None",
    round_number: int = 0,
) -> None:
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
        "links": links,
        "link_metrics": link_metrics or {},
        "failed_links": failed_links or [],
        "degraded_links": degraded_links or [],
        "recovery_path_links": recovery_path_links or [],
        "active_recovery_path": active_recovery_path,
        "round_number": round_number,
    }
    _atomic_write(STATE_FILE, state)


def _write_metrics(
    successful: int,
    failed: int,
    avg_time: float,
    total: int,
    recovery_active: bool,
    last_recovery: dict,
) -> None:
    data = {
        "successful_recoveries": successful,
        "failed_recoveries": failed,
        "average_recovery_time_sec": round(avg_time, 3),
        "total_recoveries_count": total,
        "recovery_active": recovery_active,
        "last_recovery": last_recovery,
    }
    _atomic_write(METRICS_FILE, data)


# ── Link state generators ──────────────────────────────────────────────────

def _normal_links() -> dict:
    return {lk: "up" for lk in ALL_LINKS}


def _warning_links() -> dict:
    links = {lk: "up" for lk in ALL_LINKS}
    links["s4-s8"] = "warning"
    return links


def _critical_links() -> dict:
    links = {lk: "up" for lk in ALL_LINKS}
    links["s4-s8"] = "down"
    return links


def _recovering_links() -> dict:
    return _critical_links()


def _recovered_links() -> dict:
    links = _critical_links()
    for lk in RECOVERY_PATH_LINKS:
        links[lk] = "recovery"
    return links


def _restored_links() -> dict:
    return {lk: "up" for lk in ALL_LINKS}


# ── Terminal helpers ───────────────────────────────────────────────────────

def _phase_banner(num: int, title: str, emoji: str, colour: str, desc: str) -> None:
    bar = "═" * 70
    print(f"\n{BOLD}{colour}{bar}")
    print(f"  {emoji}  PHASE {num}: {title.upper()}".center(70))
    print(f"{bar}{RESET}")
    print(f"  {DIM}{desc}{RESET}\n")


def _ticker(phase_num: int, label: str, colour: str, duration: int,
            status_fn=None, round_offset: int = 0) -> None:
    start = time.time()
    tick = 0
    while True:
        elapsed = time.time() - start
        remaining = max(0, duration - elapsed)
        tick += 1
        if status_fn:
            status_fn(tick + round_offset)
        bar_len = 30
        filled = int((elapsed / duration) * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        pct = min(100, int((elapsed / duration) * 100))
        print(
            f"\r  {colour}[Phase {phase_num}]{RESET}  "
            f"{bar} {pct:3d}%  "
            f"{DIM}{remaining:.1f}s remaining{RESET}   ",
            end="",
            flush=True,
        )
        if elapsed >= duration:
            print()
            break
        time.sleep(TICK_INTERVAL)


# ── Simulation phases ──────────────────────────────────────────────────────

def _sim_phase1(round_offset: int) -> int:
    _phase_banner(1, "NORMAL STATE", "🟢", GREEN,
                  "[SIMULATION] Light TCP background traffic. 0% loss, low latency, health=100.")
    _append_event("[SIM-DEMO] Phase 1: NORMAL STATE", "INFO")

    def update(rnd):
        _write_state(
            ai_status="NORMAL", health_score=100, health_label="Healthy",
            confidence=99.0,
            explanation=f"Network healthy — 100/100, 0.0% loss, {3.5 + rnd * 0.05:.1f}ms avg RTT",
            packet_loss_pct=0.0, rtt_avg_ms=3.5 + rnd * 0.05,
            recovery_status="NORMAL", links=_normal_links(),
            round_number=round_offset + rnd,
        )

    _ticker(1, "NORMAL", GREEN, PHASE_DURATION_SEC, update, round_offset)
    print(f"  {GREEN}✓ Dashboard shows NORMAL — Health 100/100, 0% packet loss{RESET}")
    return round_offset + PHASE_DURATION_SEC


def _sim_phase2(round_offset: int) -> int:
    _phase_banner(2, "WARNING STATE (CONGESTION)", "🟡", YELLOW,
                  "[SIMULATION] Degrading link s4-s8: 10% loss + 35ms delay. Health → 80.")
    _append_event("[SIM-DEMO] Phase 2: WARNING — Congestion on s4-s8", "WARNING")

    def update(rnd):
        progress = rnd / max(1, (PHASE_DURATION_SEC // TICK_INTERVAL))
        loss = min(12.0, 2.0 + progress * 10.0)
        rtt  = min(50.0, 5.0 + progress * 45.0)
        hs   = max(76, 100 - int(progress * 24))
        _write_state(
            ai_status="WARNING", health_score=hs, health_label="Degraded",
            confidence=87.0,
            explanation=f"Degraded conditions on s4-s8 — {rtt:.1f}ms, {loss:.1f}% loss",
            packet_loss_pct=loss, rtt_avg_ms=rtt, recovery_status="DEGRADED",
            links=_warning_links(),
            link_metrics={"s4-s8": {"loss_pct": round(loss, 2), "latency_ms": round(rtt, 2), "status": "warning"}},
            degraded_links=["s4-s8"], round_number=round_offset + rnd,
        )

    _ticker(2, "WARNING", YELLOW, PHASE_DURATION_SEC, update, round_offset)
    print(f"  {YELLOW}✓ Dashboard shows WARNING — Health ~80/100, 10% loss, 35ms latency{RESET}")
    return round_offset + PHASE_DURATION_SEC


def _sim_phase3(round_offset: int) -> int:
    _phase_banner(3, "CRITICAL STATE (LINK FAILURE)", "🔴", RED,
                  "[SIMULATION] Link s4-s8 cut. AI predicts CRITICAL at 100% conf.")
    _append_event("[SIM-DEMO] Phase 3: CRITICAL — Link s4-s8 DOWN", "CRITICAL")

    def update(rnd):
        _write_state(
            ai_status="CRITICAL", health_score=0, health_label="Critical",
            confidence=100.0,
            explanation="Critical failure on s4-s8 (access layer) — 100% packet loss",
            packet_loss_pct=100.0, rtt_avg_ms=0.0, recovery_status="NORMAL",
            links=_critical_links(),
            link_metrics={"s4-s8": {"loss_pct": 100.0, "latency_ms": 0.0, "status": "down"}},
            failed_links=["s4-s8"], round_number=round_offset + rnd,
        )
        _write_metrics(0, 0, 0.0, 0, False, {})

    _ticker(3, "CRITICAL", RED, PHASE_DURATION_SEC, update, round_offset)
    print(f"  {RED}✓ Dashboard shows CRITICAL — Health 0/100, 100% loss on s4-s8{RESET}")
    return round_offset + PHASE_DURATION_SEC


def _sim_phase4(round_offset: int) -> int:
    _phase_banner(4, "RECOVERING STATE", "🟠", MAGENTA,
                  "[SIMULATION] Confidence gate ≥80% + active_failures>0 → RecoveryEngine called.")
    _append_event("[SIM-DEMO] Phase 4: RECOVERING — SDN reroute in progress", "RECOVERY")

    _write_metrics(
        successful=0, failed=0, avg_time=0.0, total=0,
        recovery_active=True,
        last_recovery={
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "RECOVERING", "failed_link": "s4-s8",
            "selected_path": "Path_1", "route": RECOVERY_ROUTE_STR, "duration_sec": 0.0,
        },
    )

    steps = [
        "Excluding failed links from graph: s4-s8",
        f"Evaluating Path_1 via {RECOVERY_ROUTE_STR} (Score: 88/100)",
        "Safety check: no failed links in Path_1 ✓",
        "Sending flow_mod to POX Controller (http://127.0.0.1:8080/reroute)",
        "POX enforce_scenario() pushing rules to all 12 switches…",
        "OpenFlow rules applied on s8, s5, s1, s6, s12",
        "Waiting for convergence (1.5s)…",
        "Launching verification ping: h1 → h24 (10.0.5.4)…",
    ]

    start = time.time()
    for i, step in enumerate(steps):
        elapsed = time.time() - start
        pct = min(100, int((i / len(steps)) * 100))
        bar_filled = int((i / len(steps)) * 30)
        bar = "█" * bar_filled + "░" * (30 - bar_filled)
        _write_state(
            ai_status="CRITICAL", health_score=0, health_label="Critical",
            confidence=100.0,
            explanation=f"Initiating dynamic SDN recovery for failed links: s4-s8",
            packet_loss_pct=100.0, rtt_avg_ms=0.0, recovery_status="RECOVERING",
            links=_recovering_links(),
            link_metrics={"s4-s8": {"loss_pct": 100.0, "latency_ms": 0.0, "status": "down"}},
            failed_links=["s4-s8"], round_number=round_offset + i,
        )
        print(f"\r  {MAGENTA}[Phase 4]{RESET}  {bar} {pct:3d}%  {DIM}{step}{RESET}     ", end="", flush=True)
        time.sleep(max(0.5, PHASE_DURATION_SEC / len(steps)))

    print(f"\n  {MAGENTA}✓ Dashboard shows RECOVERING — POX rerouting via {RECOVERY_ROUTE_STR}{RESET}")
    return round_offset + len(steps)


def _sim_phase5(round_offset: int) -> int:
    _phase_banner(5, "RECOVERED STATE", "🔵", BLUE,
                  f"[SIMULATION] Bypass rules active via {RECOVERY_ROUTE_STR}. Ping 4/4 OK.")
    _append_event(f"[SIM-DEMO] Phase 5: RECOVERED — Path_1 live via s5-s1-s6", "RESTORED")

    recovery_dur = round(3.5 + 0.3 * (PHASE_DURATION_SEC / 8), 3)
    _write_metrics(
        successful=1, failed=0, avg_time=recovery_dur, total=1,
        recovery_active=False,
        last_recovery={
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "SUCCESS", "failed_link": "s4-s8",
            "selected_path": "Path_1", "route": RECOVERY_ROUTE_STR, "duration_sec": recovery_dur,
        },
    )

    def update(rnd):
        _write_state(
            ai_status="NORMAL", health_score=92, health_label="Healthy",
            confidence=99.0,
            explanation=f"RECOVERED via Path_1 ({RECOVERY_ROUTE_STR}) — bypass active",
            packet_loss_pct=0.0, rtt_avg_ms=8.2,
            recovery_status=f"RECOVERED (Path_1: {RECOVERY_ROUTE_STR})",
            links=_recovered_links(), link_metrics={},
            failed_links=["s4-s8"], recovery_path_links=RECOVERY_PATH_LINKS,
            active_recovery_path="Path_1", round_number=round_offset + rnd,
        )

    _ticker(5, "RECOVERED", BLUE, PHASE_DURATION_SEC, update, round_offset)
    print(f"  {BLUE}✓ Dashboard shows RECOVERED — Blue bypass path highlighted, health 92/100{RESET}")
    return round_offset + PHASE_DURATION_SEC


def _sim_phase6(round_offset: int) -> int:
    _phase_banner(6, "RESTORED NORMAL STATE", "🟢", GREEN,
                  "[SIMULATION] s4-s8 back up. POX reset to full-mesh. Health returns to 100.")
    _append_event("[SIM-DEMO] Phase 6: RESTORED NORMAL — Full-mesh restored", "RESTORED")

    _write_metrics(
        successful=1, failed=0, avg_time=3.5, total=1,
        recovery_active=False,
        last_recovery={
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "RESTORED", "failed_link": "",
            "selected_path": "None", "route": "None", "duration_sec": 0.0,
        },
    )

    def update(rnd):
        progress = rnd / max(1, (PHASE_DURATION_SEC // TICK_INTERVAL))
        hs = min(100, 92 + int(progress * 8))
        _write_state(
            ai_status="NORMAL", health_score=hs, health_label="Healthy",
            confidence=99.0,
            explanation=f"Network healthy — {hs}/100, 0.0% loss, 3.8ms avg RTT",
            packet_loss_pct=0.0, rtt_avg_ms=3.8, recovery_status="NORMAL",
            links=_restored_links(), link_metrics={},
            failed_links=[], recovery_path_links=[],
            active_recovery_path="None", round_number=round_offset + rnd,
        )

    _ticker(6, "RESTORED", GREEN, PHASE_DURATION_SEC, update, round_offset)
    print(f"  {GREEN}✓ Dashboard shows NORMAL — Full-mesh active, health 100/100{RESET}")
    return round_offset + PHASE_DURATION_SEC


def _check_permissions() -> None:
    bad = []
    for f in [STATE_FILE, METRICS_FILE, EVENTS_LOG]:
        if f.exists() and not os.access(str(f), os.W_OK):
            bad.append(f.name)
    if bad:
        print(f"\n  {YELLOW}⚠  Some result files are root-owned:{RESET}")
        for n in bad:
            print(f"     {DIM}results/{n}{RESET}")
        print(f"  {CYAN}Fix with:{RESET}  sudo chmod 666 /home/wifi/pathgaurd/results/*.json "
              f"/home/wifi/pathgaurd/results/events.log\n")


def run_simulation_demo() -> None:
    """Run the FAKE simulation demo for dashboard-only testing."""
    print(f"""
{BOLD}{RED}╔══════════════════════════════════════════════════════════════════════╗
║  ⚠️  WARNING: SIMULATION MODE  —  FAKE DASHBOARD STATE INJECTION     ║
╠══════════════════════════════════════════════════════════════════════╣
║  This mode directly writes runtime_state.json with hardcoded values. ║
║  It does NOT use: Mininet, ML model, OpenFlow, BFS, real telemetry.  ║
║                                                                      ║
║  Use ONLY for dashboard layout testing without SDN infrastructure.   ║
║  DO NOT present this as real AI/SDN behavior.                        ║
╠══════════════════════════════════════════════════════════════════════╣
║  For the REAL demo:  sudo python3 demo/fast_demo.py  (no flags)      ║
║  Or one-click:       sudo ./demo/run_real_fast_demo.sh               ║
╚══════════════════════════════════════════════════════════════════════╝{RESET}
""")

    print(f"  {DIM}Dashboard URL: {RESET}{BOLD}http://localhost:5000{RESET}  ← open this in your browser")
    print(f"  {DIM}State file:    {RESET}{STATE_FILE}")
    print(f"  {DIM}Phase duration:{RESET} {PHASE_DURATION_SEC}s per phase  "
          f"({CYAN}edit PHASE_DURATION_SEC at the top to adjust{RESET})\n")

    _check_permissions()
    _append_event("=" * 40, "INFO")
    _append_event(f"[SIMULATION] Starting new simulation demo at "
                  f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}", "INFO")
    _append_event("=" * 40, "INFO")

    total_start = time.time()
    rnd = 0

    try:
        rnd = _sim_phase1(rnd)
        rnd = _sim_phase2(rnd)
        rnd = _sim_phase3(rnd)
        rnd = _sim_phase4(rnd)
        rnd = _sim_phase5(rnd)
        rnd = _sim_phase6(rnd)

    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}⚠  Demo interrupted — resetting to NORMAL state...{RESET}")
        _write_state(
            ai_status="NORMAL", health_score=100, health_label="Healthy",
            confidence=99.0, explanation="Demo stopped — network reset to baseline",
            packet_loss_pct=0.0, rtt_avg_ms=3.5, recovery_status="NORMAL",
            links=_restored_links(), round_number=rnd,
        )
        _write_metrics(0, 0, 0.0, 0, False, {})

    elapsed = time.time() - total_start
    print(f"""
{BOLD}{YELLOW}╔══════════════════════════════════════════════════════════════════════╗
║  [SIMULATION] PathGuard Simulation Demo Complete                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  Total runtime : {elapsed:5.1f}s   Phases: 6/6                            ║
║  ⚠️  This was SIMULATED — run without --simulate for the REAL demo    ║
╚══════════════════════════════════════════════════════════════════════╝{RESET}
""")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="PathGuard Demo Launcher — real AI-driven SDN demo by default",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # REAL demo (requires sudo + POX + model.pkl):
  sudo python3 demo/fast_demo.py

  # One-click real demo with pre-flight checks:
  sudo ./demo/run_real_fast_demo.sh

  # SIMULATION ONLY — fake dashboard states, no Mininet/ML:
  python3 demo/fast_demo.py --simulate
        """,
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="[FAKE] Write hardcoded dashboard states directly (no Mininet/ML/POX). "
             "For dashboard layout testing ONLY — not for real demo.",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Skip the 'Press ENTER' prompt in the launcher script.",
    )
    args = parser.parse_args()

    if args.simulate:
        run_simulation_demo()
    else:
        run_real_demo(no_pause=args.no_pause)


if __name__ == "__main__":
    main()
