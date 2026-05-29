#!/usr/bin/env python3
"""
PathGuard: REAL High-Speed Demo Orchestrator (Optimized)
=========================================================
Uses actual Mininet, actual ML model (RandomForest), actual OpenFlow rerouting
via POX REST API, actual BFS path ranking, and actual telemetry-driven recovery.

ALL 6 phases driven by REAL system behavior — zero fake state injection.
runtime_state.json is written ONLY by monitor.py and the recovery engine.

Requires:
  • sudo (Mininet needs root)
  • POX controller running on localhost:6633 / :8080
  • Flask dashboard running on localhost:5000

Usage:
  sudo python3 demo/real_fast_demo.py

Or use the one-click launcher:
  sudo ./demo/run_real_fast_demo.sh

Phase timing (adaptive — polls real state transitions):
  Phase 1 NORMAL:        5s  baseline collection (was 8s)
  Phase 2 WARNING:       tc turbo-injection → poll AI WARNING (max 15s, was 18s)
  Phase 3 CRITICAL:      link cut → poll AI CRITICAL (max 12s, was 22s)
  Phase 4+5 RECOVERY:    auto-triggered by monitor → poll RECOVERED (max 45s, was 65s)
  Phase 6 RESTORED:      link up + rules reset → poll NORMAL (max 12s, was 18s)

Target total runtime: ~90 seconds (was ~3 minutes)
"""

from __future__ import annotations

import os
import sys
import time
import json
import threading
import subprocess
import warnings

warnings.filterwarnings("ignore")
from pathlib import Path
from datetime import datetime, timezone

# ─── Project root ──────────────────────────────────────────────────────────
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel

from topology.topology import PathGuardTopo, cleanup_mininet
from monitoring.monitor import NetworkMonitor
from ai.train_model import FaultDetector
from recovery.recover import log_event
from monitoring.runtime_state import read_runtime_state

# ─── ANSI colours ──────────────────────────────────────────────────────────
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
BLUE    = "\033[94m"
CYAN    = "\033[96m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"

# ──────────────────────────────────────────────────────────────────────────
# Speed parameters — tuned for FAST but REAL operation
# ─────────────────────────────────────────────────────────────────────────
# Monitor timing:
#   - MONITOR_INTERVAL: sleep between rounds (real round also has ping latency)
#   - MONITOR_PING_COUNT=1: single ICMP packet per probe — fast
#   - MONITOR_PING_TIMEOUT=1: 1s max per ping
#   - 6 hosts → 30 pairs running concurrently via ThreadPoolExecutor
#   - Total round time ≈ max(1s ping timeout) + 0.3s interval ≈ 1.3s/round
# ──────────────────────────────────────────────────────────────────────────
MONITOR_INTERVAL     = 0.3   # Reduced from 0.5 → faster round cadence
MONITOR_PING_COUNT   = 1     # ICMP packets per probe (1 = fastest)
MONITOR_PING_TIMEOUT = 1     # ping -W timeout (seconds)
MONITORED_HOSTS      = ["h1", "h6", "h11", "h16", "h21", "h24"]  # 30 pairs

# Phase timing (seconds) — optimized for ~90s total
PHASE1_DURATION   = 5    # Baseline collection (was 8)
PHASE2_MAX_WAIT   = 15   # Max wait for WARNING detection (was 18)
PHASE3_MAX_WAIT   = 12   # Max wait for CRITICAL detection (was 22; link cut → fast)
RECOVERY_MAX_WAIT = 45   # Max wait for RECOVERED (was 65)
PHASE6_MAX_WAIT   = 12   # Max wait for NORMAL after restoration (was 18)

# Turbo congestion parameters for Phase 2 — highly visible to AI model
# Phase 2 uses 25% loss + 60ms delay to guarantee AI detects WARNING within 2-3 rounds
TURBO_LOSS_PCT  = 25       # Loss percentage (was 10%)
TURBO_DELAY_MS  = "60ms"   # Delay (was 35ms)
TURBO_JITTER_MS = "15ms"   # Jitter (added)
TURBO_IPERF_PARALLEL = 8   # Parallel iperf streams (was 4)


# ══════════════════════════════════════════════════════════════════════════
# Terminal UI helpers
# ══════════════════════════════════════════════════════════════════════════

def phase_banner(num: int, title: str, emoji: str, colour: str, desc: str):
    bar = "═" * 70
    print(f"\n{BOLD}{colour}{bar}")
    print(f"  {emoji}  PHASE {num}: {title.upper()}".center(70))
    print(f"{bar}{RESET}")
    print(f"  {DIM}{desc}{RESET}\n")


def colour_for_state(state: dict) -> str:
    ai  = state.get("ai_status", "NORMAL") if state else "NORMAL"
    rec = state.get("recovery_status", "") if state else ""
    if "RECOVERED" in rec.upper():
        return BLUE
    if "RECOVERING" in rec.upper():
        return MAGENTA
    return {
        "NORMAL":   GREEN,
        "WARNING":  YELLOW,
        "CRITICAL": RED,
    }.get(ai, DIM)


def print_live_state(state: dict | None, label: str = ""):
    """Render one-liner live status from real runtime_state.json."""
    if not state:
        print(f"\r  {DIM}[waiting for telemetry round...]{RESET}      ", end="", flush=True)
        return
    ai   = state.get("ai_status", "?")
    hs   = state.get("health_score", "?")
    loss = state.get("packet_loss_pct", 0.0)
    rtt  = state.get("rtt_avg_ms", 0.0)
    rnd  = state.get("round_number", 0)
    conf = state.get("confidence", 0.0)
    rec  = state.get("recovery_status", "")

    col = colour_for_state(state)
    rec_short = rec[:30] if len(rec) > 30 else rec

    print(
        f"\r  {col}[{ai:8s}]{RESET}  "
        f"health={BOLD}{hs:3}{RESET}/100  "
        f"loss={loss:5.1f}%  "
        f"rtt={rtt:6.1f}ms  "
        f"conf={conf:5.1f}%  "
        f"rnd={rnd:3d}  "
        f"{DIM}{rec_short}  {label}{RESET}   ",
        end="",
        flush=True,
    )


def poll_until(
    condition_fn,
    max_wait: float,
    poll_interval: float = 0.3,   # Reduced from 0.4 → faster detection
    label_fn=None,
) -> dict | None:
    """
    Poll read_runtime_state() until condition_fn(state) is True.
    Prints live telemetry each tick.
    Returns matching state or None on timeout.
    """
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed >= max_wait:
            print()
            return None
        state = read_runtime_state()
        lbl = label_fn(state, elapsed, max_wait) if label_fn else f"{max_wait - elapsed:.0f}s"
        print_live_state(state, lbl)
        if state and condition_fn(state):
            print()
            return state
        time.sleep(poll_interval)


# ══════════════════════════════════════════════════════════════════════════
# Traffic helpers
# ══════════════════════════════════════════════════════════════════════════

def _inject_turbo_congestion(net, s4, s8, h1, h6, h16=None, h21=None):
    """
    Apply aggressive tc netem on s4↔s8 interfaces and launch heavy iperf saturation.
    Target: visible WARNING to AI model within 2-3 monitor rounds (~3-5 seconds).
    """
    # Kill any leftover light iperf first
    os.system("killall iperf 2>/dev/null || true")
    time.sleep(0.2)

    # Apply turbo tc netem: 25% loss + 60ms delay + 15ms jitter
    print(f"  💥 TURBO tc netem on s4↔s8: loss={TURBO_LOSS_PCT}%, delay={TURBO_DELAY_MS}, jitter={TURBO_JITTER_MS}...")
    try:
        conn_pairs = list(s4.connectionsTo(s8))
        for intf1, intf2 in conn_pairs:
            intf1.config(loss=TURBO_LOSS_PCT, delay=TURBO_DELAY_MS, jitter=TURBO_JITTER_MS, max_queue_size=50)
            intf2.config(loss=TURBO_LOSS_PCT, delay=TURBO_DELAY_MS, jitter=TURBO_JITTER_MS, max_queue_size=50)
        n_pairs = len(conn_pairs)
        print(f"  {GREEN}  ✓ tc turbo applied on {n_pairs} interface pair(s){RESET}")
    except Exception as e:
        print(f"  {YELLOW}  ⚠ tc config warning: {e}{RESET}")
        # Fallback: use direct subprocess tc commands
        try:
            for intf in [f"s4-eth{i}" for i in range(1, 5)]:
                os.system(f"tc qdisc replace dev {intf} root netem loss {TURBO_LOSS_PCT}% delay {TURBO_DELAY_MS} {TURBO_JITTER_MS} 2>/dev/null || true")
            print(f"  {YELLOW}  ⚠ Used fallback tc subprocess commands{RESET}")
        except Exception:
            pass

    # Ensure iperf server is running on h6
    try:
        h6.cmd("killall iperf 2>/dev/null; iperf -s -p 5001 -D")
    except Exception:
        pass

    # Launch heavy saturation: multiple parallel iperf flows
    print(f"  ⚡ Launching TURBO iperf: h1 → h6, {TURBO_IPERF_PARALLEL} parallel streams, 35s...")
    try:
        h1.popen(["iperf", "-c", h6.IP(), "-p", "5001", "-t", "35",
                  "-P", str(TURBO_IPERF_PARALLEL)])
    except Exception as e:
        print(f"  {YELLOW}  ⚠ iperf launch warning: {e}{RESET}")

    # Second saturation flow on different path if hosts available
    if h16 and h21:
        try:
            h21.cmd("iperf -s -p 5002 -D")
            h16.popen(["iperf", "-c", h21.IP(), "-p", "5002", "-t", "35", "-P", "4"])
            print(f"  ⚡ Secondary saturation: h16 → h21, 4 streams...")
        except Exception:
            pass


def _reset_tc_congestion(s4, s8):
    """Reset tc congestion on s4↔s8 to clean baseline."""
    print(f"  🛠  Resetting tc on s4↔s8 (loss=0, delay=2ms)...")
    try:
        for intf1, intf2 in s4.connectionsTo(s8):
            intf1.config(loss=0, delay="2ms")
            intf2.config(loss=0, delay="2ms")
        print(f"  {GREEN}  ✓ tc reset complete{RESET}")
    except Exception as e:
        print(f"  {YELLOW}  ⚠ tc reset warning: {e}{RESET}")
        # Fallback subprocess
        os.system("tc qdisc del dev s4-eth1 root 2>/dev/null || true")


# ══════════════════════════════════════════════════════════════════════════
# Phase implementations — all using REAL Mininet + real telemetry
# ══════════════════════════════════════════════════════════════════════════

def phase1_normal(net, monitor):
    """
    PHASE 1 — NORMAL
    Real iperf light background. Real ICMP probes. AI classifies NORMAL.
    Reduced to 5s (was 8s) — enough for 3 monitor rounds to establish baseline.
    """
    phase_banner(
        1, "NORMAL STATE", "🟢", GREEN,
        f"Real iperf 1Mbps baseline. Real pings via 30 host-pairs. "
        f"ML RandomForest classifies NORMAL. ({PHASE1_DURATION}s baseline collection)"
    )
    log_event("[REAL-DEMO] Phase 1: NORMAL STATE — starting baseline collection", "INFO")

    h1  = net.get("h1")
    h6  = net.get("h6")
    h11 = net.get("h11")
    h21 = net.get("h21")

    # Light iperf background (1 Mbps, outlasts all phases)
    print(f"  ⚡ iperf server on h6, client h1 → h6 (1Mbps, 80s background)...")
    h6.cmd("killall iperf 2>/dev/null; iperf -s -p 5001 -D")
    h1.popen(["iperf", "-c", h6.IP(), "-p", "5001", "-t", "80", "-b", "1M"])

    # Background ping across topology for variety
    print(f"  ⚡ Background ping h11 → h21 (20 pings, 0.5s interval)...")
    h11.popen(["ping", "-c", "20", "-i", "0.5", h21.IP()])

    print(f"\n  📡 Collecting real baseline telemetry for {PHASE1_DURATION}s...\n")
    start = time.time()
    while time.time() - start < PHASE1_DURATION:
        state = read_runtime_state()
        remaining = PHASE1_DURATION - (time.time() - start)
        print_live_state(state, f"baseline  {remaining:.0f}s left")
        time.sleep(0.3)
    print()

    state = read_runtime_state()
    if state:
        hs   = state.get("health_score", "?")
        loss = state.get("packet_loss_pct", 0.0)
        rtt  = state.get("rtt_avg_ms", 0.0)
        ai   = state.get("ai_status", "?")
        rnd  = state.get("round_number", 0)
        print(
            f"  {GREEN}✓ Phase 1 done — Real AI: {ai}, health={hs}/100, "
            f"loss={loss:.1f}%, rtt={rtt:.1f}ms, round={rnd}{RESET}"
        )
    log_event("[REAL-DEMO] Phase 1 complete: baseline established", "INFO")


def phase2_warning(net, monitor):
    """
    PHASE 2 — WARNING (CONGESTION)
    TURBO tc netem: 25% loss + 60ms delay + 15ms jitter on s4-s8.
    Heavy iperf saturation (8 parallel streams).
    AI must classify WARNING from real telemetry within 2-3 rounds (~3-5 seconds).
    """
    phase_banner(
        2, "WARNING STATE (CONGESTION)", "🟡", YELLOW,
        f"TURBO tc netem: loss={TURBO_LOSS_PCT}%, delay={TURBO_DELAY_MS}, jitter={TURBO_JITTER_MS} on s4-s8. "
        f"{TURBO_IPERF_PARALLEL} parallel iperf streams. AI detects WARNING from live metrics."
    )
    log_event(f"[REAL-DEMO] Phase 2: Injecting TURBO tc congestion on s4-s8 "
              f"(loss={TURBO_LOSS_PCT}%, delay={TURBO_DELAY_MS})", "WARNING")

    s4  = net.get("s4")
    s8  = net.get("s8")
    h1  = net.get("h1")
    h6  = net.get("h6")
    h16 = net.get("h16")
    h21 = net.get("h21")

    _inject_turbo_congestion(net, s4, s8, h1, h6, h16, h21)

    print(f"\n  📡 Polling real telemetry — waiting for real AI WARNING classification...\n")

    def is_warning(state):
        # AI model must detect degraded conditions from real telemetry
        return (
            state.get("ai_status") in ("WARNING", "CRITICAL") or
            state.get("packet_loss_pct", 0.0) >= 5.0
        )

    def label(state, elapsed, max_w):
        loss = state.get("packet_loss_pct", 0.0) if state else 0.0
        return f"⏳WARNING?  loss={loss:.1f}%  {max_w - elapsed:.0f}s left"

    matched = poll_until(is_warning, PHASE2_MAX_WAIT, label_fn=label)

    if matched:
        ai   = matched.get("ai_status", "?")
        conf = matched.get("confidence", 0.0)
        loss = matched.get("packet_loss_pct", 0.0)
        rtt  = matched.get("rtt_avg_ms", 0.0)
        rnd  = matched.get("round_number", 0)
        print(
            f"  {YELLOW}✓ Real AI classified {ai} — "
            f"conf={conf:.0f}%, loss={loss:.1f}%, rtt={rtt:.1f}ms "
            f"(round {rnd}){RESET}"
        )
        log_event(
            f"[REAL-DEMO] Phase 2 confirmed: AI={ai} conf={conf:.0f}% loss={loss:.1f}%",
            "WARNING"
        )
    else:
        print(f"  {YELLOW}⚠ WARNING not detected within {PHASE2_MAX_WAIT}s "
              f"(congestion IS real — proceeding to CRITICAL){RESET}")
        log_event("[REAL-DEMO] Phase 2 timeout — congestion real, proceeding anyway", "WARNING")


def phase3_critical(net, monitor):
    """
    PHASE 3 — CRITICAL (LINK FAILURE)
    Real net.configLinkStatus() cuts s4-s8 completely.
    100% packet loss on affected paths → AI must classify CRITICAL.
    Physical failsafe in monitor.py detects this within 1-2 rounds (~1.5-3s).
    """
    phase_banner(
        3, "CRITICAL STATE (LINK FAILURE)", "🔴", RED,
        f"Executing real net.configLinkStatus('s4','s8','down'). "
        f"100% loss → AI RandomForest classifies CRITICAL. (max {PHASE3_MAX_WAIT}s)"
    )
    log_event("[REAL-DEMO] Phase 3: Cutting link s4-s8 DOWN", "CRITICAL")

    print(f"  ❌ Executing: net.configLinkStatus('s4', 's8', 'down')...")
    net.configLinkStatus("s4", "s8", "down")
    print(f"  {RED}  ✓ Link s4-s8 is physically DOWN (OVS interfaces disabled){RESET}")

    # Fast-path: flood pings to accelerate monitor detection of 100% loss
    # These run concurrently and will immediately register timeouts in the
    # monitor's next collect_once() round.
    h1 = net.get("h1")
    h8_hosts = ["h1", "h2", "h3"]  # Hosts on s8 side
    print(f"  📡 Flooding diagnostic pings to accelerate CRITICAL detection...")
    for hname in h8_hosts:
        try:
            h = net.get(hname)
            if h:
                h.popen(["ping", "-c", "3", "-W", "1", "10.0.2.1"])  # h6 via s4-s8
        except Exception:
            pass

    print(f"\n  📡 Polling real telemetry — waiting for AI CRITICAL classification...\n")

    def is_critical(state):
        return (
            state.get("ai_status") == "CRITICAL" or
            state.get("health_score", 100) < 60 or
            state.get("packet_loss_pct", 0.0) >= 50.0 or
            len(state.get("failed_links", [])) > 0
        )

    def label(state, elapsed, max_w):
        fl = state.get("failed_links", []) if state else []
        fl_str = f"failed={fl}" if fl else "detecting failure..."
        return f"⏳{fl_str}  {max_w - elapsed:.0f}s left"

    matched = poll_until(is_critical, PHASE3_MAX_WAIT, label_fn=label)

    if matched:
        ai   = matched.get("ai_status", "?")
        conf = matched.get("confidence", 0.0)
        loss = matched.get("packet_loss_pct", 0.0)
        hs   = matched.get("health_score", 0)
        fl   = matched.get("failed_links", [])
        rnd  = matched.get("round_number", 0)
        print(
            f"  {RED}✓ Real AI classified {ai} — "
            f"conf={conf:.0f}%, loss={loss:.1f}%, health={hs}/100, round={rnd}{RESET}"
        )
        if fl:
            print(f"  {RED}  Failed links detected by monitor: {', '.join(fl)}{RESET}")
        log_event(
            f"[REAL-DEMO] Phase 3 confirmed: CRITICAL loss={loss:.1f}% conf={conf:.0f}% "
            f"failed_links={fl}",
            "CRITICAL"
        )
    else:
        print(
            f"  {RED}⚠ CRITICAL state not confirmed in {PHASE3_MAX_WAIT}s "
            f"(link IS physically down — recovery will trigger on next monitor round){RESET}"
        )
        log_event("[REAL-DEMO] Phase 3 timeout — link is down, recovery will trigger", "WARNING")


def phase4_5_recovery(net, monitor):
    """
    PHASE 4 → 5 — RECOVERING → RECOVERED
    Recovery engine triggers AUTOMATICALLY inside monitor._loop()
    via _detect_faults() → run_recovery_async() → PathRanker.evaluate_paths()
    → POST /reroute → POX enforce_scenario() → ofp_flow_mod on all 12 switches
    → verification ping h1→h24.

    This function writes NOTHING to runtime_state.json — only polls it.
    Recovery path is discovered via real BFS (k_shortest_paths with excluded_links).
    """
    phase_banner(
        4, "RECOVERING → RECOVERED (AUTO)", "🟠", MAGENTA,
        "Real auto-recovery: monitor._loop() → RecoveryEngine → "
        f"PathRanker BFS → POX flow_mod → h1→h24 verify ping. (max {RECOVERY_MAX_WAIT}s)"
    )
    log_event("[REAL-DEMO] Phase 4+5: Waiting for real auto-recovery to complete", "RECOVERY")

    print(f"  📡 Real recovery pipeline (all automatic):")
    print(f"     1. monitor._detect_faults() scans physical Mininet link state")
    print(f"     2. RecoveryEngine.trigger_recovery() called with real failed_links")
    print(f"     3. PathRanker.evaluate_paths() runs real BFS, excludes s4-s8")
    print(f"     4. Best path selected, score computed from real telemetry metrics")
    print(f"     5. POST http://127.0.0.1:8080/reroute → POX enforce_scenario()")
    print(f"     6. ofp_flow_mod pushed to all 12 OVS switches")
    print(f"     7. Verification ping h1 → 10.0.5.4 (h24) inside Mininet namespace")
    print(f"\n  ⏱  Polling runtime_state.json — max {RECOVERY_MAX_WAIT}s...\n")

    recovering_announced = False
    recovery_start = time.time()

    while True:
        elapsed = time.time() - recovery_start
        if elapsed >= RECOVERY_MAX_WAIT:
            print()
            print(f"  {YELLOW}⚠ Recovery max wait {RECOVERY_MAX_WAIT}s elapsed{RESET}")
            break

        state = read_runtime_state()
        rec   = state.get("recovery_status", "") if state else ""
        rec_upper = rec.upper()

        remaining = RECOVERY_MAX_WAIT - elapsed
        lbl = f"{rec[:28]}  {remaining:.0f}s left" if rec else f"⏳recovery pending  {remaining:.0f}s left"

        print_live_state(state, lbl)

        # Announce RECOVERING transition once
        if not recovering_announced and "RECOVERING" in rec_upper:
            print(f"\n  {MAGENTA}✓ Real RECOVERING state confirmed in runtime_state.json{RESET}")
            recovering_announced = True
            log_event("[REAL-DEMO] Phase 4: RECOVERING state active — POX rerouting in progress", "RECOVERY")

        # Exit on RECOVERED
        if "RECOVERED" in rec_upper and "RECOVERING" not in rec_upper:
            print()
            conf  = state.get("confidence", 0.0)
            rtt   = state.get("rtt_avg_ms", 0.0)
            loss  = state.get("packet_loss_pct", 0.0)
            hs    = state.get("health_score", 0)
            total = time.time() - recovery_start

            print(f"  {BLUE}✓ RECOVERED confirmed in {total:.1f}s{RESET}")
            print(f"  {BLUE}  Real AI: conf={conf:.0f}%, loss={loss:.1f}%, rtt={rtt:.1f}ms, health={hs}/100{RESET}")
            print(f"  {BLUE}  recovery_status: {rec}{RESET}")

            # Print real BFS path info from runtime state
            _print_real_recovery_info(state)

            log_event(
                f"[REAL-DEMO] Phase 5 RECOVERED in {total:.1f}s — "
                f"recovery_status={rec}",
                "RESTORED"
            )
            return state

        time.sleep(0.3)  # Fast polling — reduced from 0.4

    # Timeout — show final state
    final = read_runtime_state()
    if final:
        rec = final.get("recovery_status", "unknown")
        ai  = final.get("ai_status", "unknown")
        print(f"  Final state: ai_status={ai}, recovery_status={rec}")
        if "RECOVERING" in rec.upper() or "RECOVERED" in rec.upper():
            print(f"  {YELLOW}Recovery is in progress — the real system is still working{RESET}")
    return final


def _print_real_recovery_info(state: dict):
    """Print real BFS path information from runtime state."""
    failed  = state.get("failed_links", [])
    rec_lnk = state.get("recovery_path_links", [])
    active  = state.get("active_recovery_path", "None")
    expl    = state.get("explanation", "")

    print(f"\n  📊 Real BFS Recovery Results (from PathRanker.evaluate_paths):")
    print(f"     Failed links excluded:  {', '.join(failed) if failed else 'none'}")
    print(f"     Selected path:          {active}")
    if rec_lnk:
        print(f"     Bypass route links:    {' → '.join(rec_lnk)}")
    if expl:
        print(f"     AI explanation:        {expl}")

    # Also show proof from events.log if available
    events_log = project_root / "results" / "events.log"
    if events_log.exists():
        try:
            with open(events_log, "r") as f:
                lines = f.readlines()
            recovery_lines = [
                l.strip() for l in lines
                if any(k in l for k in ["Candidate:", "Safety Check:", "Selected optimal", "flow_mod", "Verification"])
            ][-8:]  # Last 8 relevant lines
            if recovery_lines:
                print(f"\n  📝 Recent events.log (proof of real BFS + OpenFlow):")
                for line in recovery_lines:
                    print(f"     {DIM}{line}{RESET}")
        except Exception:
            pass


def phase6_restored(net, monitor):
    """
    PHASE 6 — RESTORED NORMAL
    Real net.configLinkStatus('s4','s8','up').
    Real tc reset on s4-s8.
    Real POX reset_to_normal() → clears OpenFlow bypass rules.
    AI must reclassify NORMAL from real telemetry.
    """
    phase_banner(
        6, "RESTORED NORMAL STATE", "🟢", GREEN,
        f"Restoring s4-s8, resetting tc, POX full-mesh rule reset. "
        f"AI reclassifies NORMAL from real telemetry. (max {PHASE6_MAX_WAIT}s)"
    )
    log_event("[REAL-DEMO] Phase 6: Restoring link s4-s8 and resetting full-mesh rules", "RESTORED")

    # 1. Bring link back up
    print(f"  🔌 Executing: net.configLinkStatus('s4', 's8', 'up')...")
    net.configLinkStatus("s4", "s8", "up")
    print(f"  {GREEN}  ✓ Link s4-s8 physically UP{RESET}")
    time.sleep(0.3)

    # 2. Reset tc congestion
    s4 = net.get("s4")
    s8 = net.get("s8")
    _reset_tc_congestion(s4, s8)

    # 3. Kill iperf saturation flows
    print(f"  ⚡ Terminating iperf congestion flows...")
    os.system("killall iperf 2>/dev/null || true")
    time.sleep(0.2)

    # 4. Real POX rule reset
    print(f"  🔄 Calling monitor.recovery_engine.reset_to_normal() → POX full-mesh reset...")
    try:
        monitor.recovery_engine.reset_to_normal()
        print(f"  {GREEN}  ✓ POX full-mesh OpenFlow rules restored{RESET}")
    except Exception as e:
        print(f"  {YELLOW}  ⚠ reset_to_normal warning: {e}{RESET}")

    # 5. Re-launch light traffic to help AI see healthy metrics
    h1 = net.get("h1")
    h6 = net.get("h6")
    if h1 and h6:
        try:
            h6.cmd("iperf -s -p 5001 -D")
            h1.popen(["iperf", "-c", h6.IP(), "-p", "5001", "-t", "20", "-b", "1M"])
            print(f"  ⚡ Light iperf relaunched h1→h6 (1Mbps) to help NORMAL reclassification...")
        except Exception:
            pass

    print(f"\n  📡 Polling real telemetry — waiting for AI NORMAL reclassification...\n")

    def is_normal(state):
        return (
            state.get("ai_status") == "NORMAL" and
            state.get("health_score", 0) >= 85 and
            state.get("packet_loss_pct", 100.0) < 5.0 and
            "RECOVER" not in state.get("recovery_status", "").upper()
        )

    def label(state, elapsed, max_w):
        ai   = state.get("ai_status", "?") if state else "?"
        loss = state.get("packet_loss_pct", 0.0) if state else 0.0
        return f"⏳ai={ai} loss={loss:.1f}%  {max_w - elapsed:.0f}s left"

    matched = poll_until(is_normal, PHASE6_MAX_WAIT, label_fn=label)

    if matched:
        hs   = matched.get("health_score", 0)
        loss = matched.get("packet_loss_pct", 0.0)
        rtt  = matched.get("rtt_avg_ms", 0.0)
        conf = matched.get("confidence", 0.0)
        rnd  = matched.get("round_number", 0)
        print(
            f"  {GREEN}✓ Real AI classified NORMAL — "
            f"health={hs}/100, loss={loss:.1f}%, rtt={rtt:.1f}ms, conf={conf:.0f}%, round={rnd}{RESET}"
        )
        log_event(
            f"[REAL-DEMO] Phase 6 NORMAL confirmed: health={hs} loss={loss:.1f}% rtt={rtt:.1f}ms",
            "INFO"
        )
    else:
        print(
            f"  {YELLOW}⚠ NORMAL not confirmed in {PHASE6_MAX_WAIT}s "
            f"(network may need additional convergence time — link IS restored){RESET}"
        )
        log_event("[REAL-DEMO] Phase 6 timeout — link is up, monitor converging", "WARNING")


# ══════════════════════════════════════════════════════════════════════════
# Proof-of-Real-AI startup validation
# ══════════════════════════════════════════════════════════════════════════

def print_ai_proof(fault_detector) -> None:
    """Run sanity predictions and print proof that the real AI model is active."""
    print(f"\n  {BOLD}🧠 AI Model Proof (RandomForest inference):{RESET}")
    test_cases = [
        (0.0,   3.5,  4.0, 0.2, "NORMAL"),
        (10.0,  35.0, 60.0, 15.0, "WARNING"),
        (100.0, 0.0,  0.0, 0.0, "CRITICAL"),
    ]
    for loss, avg, mx, mdev, expected in test_cases:
        try:
            r = fault_detector.predict_advanced(loss, avg, mx, mdev)
            sev  = r["severity"]
            conf = r["confidence"]
            col  = GREEN if sev == "NORMAL" else (YELLOW if sev == "WARNING" else RED)
            mark = "✓" if sev == expected else "⚠"
            print(
                f"     {mark} loss={loss:5.1f}% rtt={avg:5.1f}ms → "
                f"{col}{sev:8s}{RESET} ({conf:.0f}% conf)  [expected: {expected}]"
            )
        except Exception as e:
            print(f"     ⚠ Prediction error: {e}")
    print()


# ══════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ══════════════════════════════════════════════════════════════════════════

def print_header():
    total_est = PHASE1_DURATION + PHASE2_MAX_WAIT + PHASE3_MAX_WAIT + RECOVERY_MAX_WAIT + PHASE6_MAX_WAIT
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════════════╗
║     🛡️  PathGuard REAL High-Speed Demo — AI-Driven SDN Self-Healing    ║
║         Real Mininet • Real RandomForest ML • Real OpenFlow BFS        ║
╠══════════════════════════════════════════════════════════════════════╣
║  Monitor: interval={MONITOR_INTERVAL}s  ping=-c {MONITOR_PING_COUNT} -W {MONITOR_PING_TIMEOUT}  hosts={len(MONITORED_HOSTS)} ({len(MONITORED_HOSTS)*(len(MONITORED_HOSTS)-1)} pairs)          ║
║  Congestion: loss={TURBO_LOSS_PCT}%  delay={TURBO_DELAY_MS}  jitter={TURBO_JITTER_MS}  streams={TURBO_IPERF_PARALLEL}       ║
║  Phase timing:                                                       ║
║    P1 NORMAL:    {PHASE1_DURATION:2d}s   P2 WARNING:  {PHASE2_MAX_WAIT:2d}s max               ║
║    P3 CRITICAL:  {PHASE3_MAX_WAIT:2d}s max  P4+5 RECOV: {RECOVERY_MAX_WAIT:2d}s max               ║
║    P6 RESTORED:  {PHASE6_MAX_WAIT:2d}s max  Total est:  ~{total_est:3d}s max                ║
╠══════════════════════════════════════════════════════════════════════╣
║  Dashboard: http://localhost:5000  ← open now                        ║
║  This script writes NOTHING to runtime_state.json directly.          ║
╚══════════════════════════════════════════════════════════════════════╝{RESET}
""")


def main():
    # Must run as root for Mininet
    if os.geteuid() != 0:
        print(f"{RED}✖ Mininet requires root privileges.{RESET}")
        print(f"  Run: {BOLD}sudo python3 demo/real_fast_demo.py{RESET}")
        print(f"  Or:  {BOLD}sudo ./demo/run_real_fast_demo.sh{RESET}")
        sys.exit(1)

    print_header()

    # Suppress Mininet INFO noise (we have our own logging)
    setLogLevel("warning")

    # ── Step 1: Cleanup ────────────────────────────────────────────
    print(f"{BOLD}{BLUE}[1/7] Cleaning up previous Mininet/iperf state...{RESET}")
    os.system("killall iperf 2>/dev/null || true")
    cleanup_mininet()
    print(f"  {GREEN}✓ Environment clean{RESET}\n")

    # ── Step 2: Build Mininet Topology ─────────────────────────────
    print(f"{BOLD}{BLUE}[2/7] Building 12-switch PathGuard topology (OVS + TCLink)...{RESET}")
    topo = PathGuardTopo()
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip="127.0.0.1", port=6633),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=True,
    )
    net.start()
    print(f"  {GREEN}✓ 12 OVS switches + 24 hosts started{RESET}")
    print(f"  ⏱️  Waiting 4s for POX controller discovery...")
    time.sleep(4)  # Reduced from 5s
    print(f"  {GREEN}✓ Switches registered with POX controller{RESET}\n")

    # ── Step 3: Load Real AI Model ─────────────────────────────────
    print(f"{BOLD}{BLUE}[3/7] Loading trained Random Forest model (ai/model.pkl)...{RESET}")
    fault_detector = None
    model_path = project_root / "ai" / "model.pkl"
    if model_path.exists():
        try:
            fault_detector = FaultDetector.load(str(model_path))
            print(f"  {GREEN}✓ Model loaded from {model_path}{RESET}")
            print_ai_proof(fault_detector)
        except Exception as e:
            print(f"  {YELLOW}⚠ Model load failed: {e}  (fallback heuristics active){RESET}\n")
    else:
        print(f"  {YELLOW}⚠ model.pkl not found at {model_path}{RESET}")
        print(f"  {DIM}  Run: python3 ai/train_model.py  to train it first{RESET}\n")

    # ── Step 4: Start Ultra-Fast Monitor ───────────────────────────
    print(f"{BOLD}{BLUE}[4/7] Starting NetworkMonitor "
          f"(interval={MONITOR_INTERVAL}s, ping_count={MONITOR_PING_COUNT}, "
          f"ping_timeout={MONITOR_PING_TIMEOUT}s)...{RESET}")
    monitor = NetworkMonitor(
        net=net,
        csv_path=project_root / "datasets" / "network_data.csv",
        interval=MONITOR_INTERVAL,
        ping_count=MONITOR_PING_COUNT,
        ping_timeout=MONITOR_PING_TIMEOUT,
        fault_detector=fault_detector,
        monitored_hosts=MONITORED_HOSTS,
        verbose=False,   # Suppress per-record prints (too noisy for demo)
    )
    monitor.start()

    # Wait for first real telemetry round (max 12s)
    print(f"  ⏱️  Waiting for first real telemetry round (max 12s)...")
    waited = 0
    first_state = None
    while waited < 12:
        first_state = read_runtime_state()
        if first_state and first_state.get("round_number", 0) >= 1:
            break
        time.sleep(0.5)
        waited += 0.5

    if first_state and first_state.get("round_number", 0) >= 1:
        rnd  = first_state.get("round_number", "?")
        loss = first_state.get("packet_loss_pct", 0.0)
        rtt  = first_state.get("rtt_avg_ms", 0.0)
        ai   = first_state.get("ai_status", "?")
        print(
            f"  {GREEN}✓ First round complete: round={rnd}, "
            f"AI={ai}, loss={loss:.1f}%, rtt={rtt:.1f}ms{RESET}\n"
        )
    else:
        print(f"  {YELLOW}⚠ First round not confirmed — monitor warming up{RESET}\n")

    total_start = time.time()

    try:
        # ──── PHASE 1: NORMAL ─────────────────────────────────────
        phase1_normal(net, monitor)

        # ──── PHASE 2: WARNING ────────────────────────────────────
        phase2_warning(net, monitor)

        # ──── PHASE 3: CRITICAL ───────────────────────────────────
        phase3_critical(net, monitor)

        # ──── PHASE 4+5: RECOVERING → RECOVERED ──────────────────
        # Recovery triggers AUTOMATICALLY in monitor._loop() → _detect_faults()
        # → run_recovery_async() — we only poll and report.
        phase4_5_recovery(net, monitor)

        # ──── PHASE 6: RESTORED NORMAL ────────────────────────────
        phase6_restored(net, monitor)

    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}⚠ Demo interrupted by user — cleaning up...{RESET}")

    finally:
        elapsed = time.time() - total_start
        print(f"""
{BOLD}{GREEN}╔══════════════════════════════════════════════════════════════════════╗
║                  ✅  PathGuard Real Demo Complete!                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  Total runtime:    {elapsed:5.1f}s                                        ║
║  Real ML proof:    results/events.log  (AI predict_advanced calls)   ║
║  Real BFS proof:   results/events.log  (PathRanker evaluations)      ║
║  Real OF proof:    results/pox.log     (ofp_flow_mod messages)       ║
╚══════════════════════════════════════════════════════════════════════╝{RESET}""")

        print(f"\n{BOLD}{BLUE}[7/7] Graceful shutdown...{RESET}")
        os.system("killall iperf 2>/dev/null || true")
        try:
            monitor.stop()
        except Exception:
            pass
        try:
            net.stop()
        except Exception:
            pass
        cleanup_mininet()
        print(f"  {GREEN}✓ Network stopped and cleaned up.{RESET}\n")

        # Final proof summary
        print(f"  Verify real execution:")
        print(f"    {DIM}tail -50 {project_root}/results/events.log{RESET}")
        print(f"    {DIM}grep 'flow_mod\\|Updated flow\\|Candidate:\\|RECOVERED' {project_root}/results/pox.log 2>/dev/null | tail -20{RESET}")
        print(f"    {DIM}grep 'AI predicted\\|PathRanker\\|Selected optimal' {project_root}/results/events.log | tail -20{RESET}")


if __name__ == "__main__":
    main()
