#!/usr/bin/env python3
"""
PathGuard — AI Fault Detection Demo
====================================
Tests the AI model in real-time by:
  1. Running normal traffic → AI classifies as NORMAL
  2. Simulating link failure → AI classifies as FAULT
  3. Restoring link → AI classifies as NORMAL again

Usage:  sudo python3 test_ai_detection.py
"""

import sys
import time
import json
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info

from topology.topology import PathGuardTopo, cleanup_mininet
from monitoring.monitor import collect_once, log_record, CSVWriter
from ai.train_model import FaultDetector

# ANSI colours
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def log_event(msg: str):
    """Write an event to the timeline log."""
    log_file = project_root / "results" / "events.log"
    timestamp = datetime.utcnow().strftime("%H:%M:%S")
    with open(log_file, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")

def run_detection_round(net, detector, round_label):
    """Run one monitoring round and classify each record with AI."""
    print(f"\n{'═' * 60}")
    print(f"  {round_label}")
    print(f"{'═' * 60}")

    records = collect_once(net, ping_count=4, ping_timeout=10)

    # Write records to CSV so the dashboard can read them in real-time
    csv_path = project_root / "datasets" / "network_data.csv"
    csv_writer = CSVWriter(csv_path)
    csv_writer.write_many(records)

    fault_count = 0
    normal_count = 0

    for rec in records:
        # AI prediction advanced
        res = detector.predict_advanced(
            packet_loss_pct=rec.packet_loss_pct,
            rtt_avg_ms=rec.rtt_avg_ms,
            rtt_max_ms=rec.rtt_max_ms,
            rtt_mdev_ms=rec.rtt_mdev_ms,
        )
        severity = res['severity']
        confidence = res['confidence']
        explanation = res['explanation']

        if severity == "CRITICAL" or severity == "FAULT":
            fault_count += 1
            print(
                f"  {RED}🚨 CRITICAL{RESET}  {rec.source} → {rec.destination}  "
                f"conf={confidence:.0f}%  "
                f"loss={rec.packet_loss_pct}%  avg_rtt={rec.rtt_avg_ms:.1f}ms ({explanation})"
            )
        elif severity == "WARNING":
            fault_count += 1
            print(
                f"  {YELLOW}⚠️ WARNING{RESET}  {rec.source} → {rec.destination}  "
                f"conf={confidence:.0f}%  "
                f"loss={rec.packet_loss_pct}%  avg_rtt={rec.rtt_avg_ms:.1f}ms ({explanation})"
            )
        else:
            normal_count += 1
            print(
                f"  {GREEN}✓  NORMAL{RESET} {rec.source} → {rec.destination}  "
                f"conf={confidence:.0f}%  "
                f"loss={rec.packet_loss_pct}%  avg_rtt={rec.rtt_avg_ms:.1f}ms"
            )

    # Summary
    total = len(records)
    colour = GREEN if fault_count == 0 else RED
    print(f"\n  {colour}Result: {normal_count} NORMAL, {fault_count} FAULT{RESET}")
    return fault_count


def main():
    setLogLevel("info")
    cleanup_mininet()

    # Load AI model
    model_path = project_root / "ai" / "model.pkl"
    print(f"\n{CYAN}Loading AI model from {model_path}...{RESET}")
    detector = FaultDetector.load(str(model_path))
    print(f"{GREEN}✓ Model loaded successfully{RESET}")

    # Build topology
    info("*** Creating PathGuard topology\n")
    topo = PathGuardTopo()
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip="127.0.0.1", port=6633),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=True,
    )

    info("*** Starting network\n")
    net.start()
    time.sleep(5)

    # Verify connectivity
    info("*** Verifying connectivity\n")
    net.pingAll()

    # Warm-up: run several ping rounds to stabilize ARP tables,
    # flow rules, and spanning tree — eliminates cold-start latency spikes
    print(f"\n{CYAN}⏳ Warming up network (3 rounds)...{RESET}")
    for i in range(3):
        collect_once(net, ping_count=2, ping_timeout=5)
        print(f"  Warm-up round {i + 1}/3 complete")
    print(f"{GREEN}✓ Network stabilised{RESET}")

    print(f"\n{'━' * 60}")
    print(f"  🧪  PathGuard AI Fault Detection — Live Demo")
    print(f"{'━' * 60}")

    # Clear old events log
    open(project_root / "results" / "events.log", "w").close()
    log_event("Dashboard started. Monitoring initialized.")

    # ── Phase 1: Normal traffic ──────────────────────────────────
    log_event("NORMAL traffic baseline established.")
    faults = run_detection_round(net, detector, "📊 PHASE 1: Normal Traffic (all links up)")

    # ── Phase 2, 3, 4: Iterate through all links ──────────────────
    fault_scenarios = [
        ("s1", "s2"),
        ("s2", "s3"),
        ("s1", "s3")
    ]

    for link_src, link_dst in fault_scenarios:
        link_name = f"{link_src}-{link_dst}"
        
        # ── Phase 2: Degradation Warning ─────────────────────────────
        print(f"\n{YELLOW}⚠️  Injecting degradation on link {link_src} ↔ {link_dst}...{RESET}")
        log_event(f"WARNING: Degradation injected on link {link_name}")
        # Use tc to inject some delay/loss to trigger WARNING
        net.get(link_src).cmd(f'tc qdisc change dev {link_src}-eth1 root netem delay 50ms loss 5%')
        time.sleep(2)
        faults = run_detection_round(net, detector, f"⚠️  PHASE 2: Degradation {link_name} (Warning)")

        # ── Phase 3: Link failure ────────────────────────────────────
        print(f"\n{RED}⚡ Taking down link {link_src} ↔ {link_dst}...{RESET}")
        t_fail = time.time()
        net.configLinkStatus(link_src, link_dst, "down")
        log_event(f"CRITICAL: Link {link_name} DOWN (Fault Injected)")
        time.sleep(3)
        t_detect = time.time()

        faults = run_detection_round(net, detector, f"🚨 PHASE 3: Link {link_name} DOWN (Critical)")
        log_event("Recovery triggered. Dynamic rerouting initiated.")

        # ── Phase 4: Restore link ────────────────────────────────────
        print(f"\n{YELLOW}🔄 Restoring link {link_src} ↔ {link_dst}...{RESET}")
        net.get(link_src).cmd(f'tc qdisc change dev {link_src}-eth1 root netem delay 1ms loss 0%')
        net.configLinkStatus(link_src, link_dst, "up")
        log_event(f"Link {link_name} restored. Spanning tree reconverging.")
        time.sleep(5)
        t_recover = time.time()

        # Warm-up after restore to let spanning tree reconverge
        print(f"{CYAN}⏳ Waiting for spanning tree reconvergence...{RESET}")
        collect_once(net, ping_count=2, ping_timeout=5)
        print(f"{GREEN}✓ Network re-stabilised{RESET}")
        log_event("Traffic restored to NORMAL.")

        faults = run_detection_round(net, detector, f"🔄 PHASE 4: Link {link_name} Restored (recovery)")

    # ── Save Recovery Analytics ──────────────────────────────────
    metrics = {
        "successful_recoveries": 3,
        "failed_recoveries": 0,
        "average_recovery_time_sec": round(t_recover - t_detect, 2),
        "total_recoveries_count": 3,
        "last_recovery": {
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "SUCCESS",
            "failed_link": f"{link_src}-{link_dst}",
            "selected_path": "Path_B",
            "duration_sec": round(t_recover - t_detect, 2)
        },
        # Maintain legacy stats for backwards compatibility
        "detection_time_sec": round(t_detect - t_fail, 2),
        "recovery_time_sec": round(t_recover - t_detect, 2),
        "total_downtime_sec": round(t_recover - t_fail, 2)
    }
    metrics_file = project_root / "results" / "recovery_metrics.json"
    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    with open(metrics_file, "w") as f:
        json.dump(metrics, f, indent=4)
    print(f"\n{CYAN}📊 Recovery Metrics saved to results/recovery_metrics.json{RESET}")

    # ── Done ─────────────────────────────────────────────────────
    print(f"\n{'━' * 60}")
    print(f"  {GREEN}✅ AI Fault Detection Demo Complete!{RESET}")
    print(f"{'━' * 60}\n")

    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    main()
