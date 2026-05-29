#!/usr/bin/env python3
"""
PathGuard: End-to-End Self-Healing Lifecycle Orchestrator
--------------------------------------------------------
Validates the complete network state transitions:
  NORMAL ➔ WARNING ➔ CRITICAL ➔ RECOVERING ➔ RECOVERED ➔ NORMAL (Restored)

Outputs a comprehensive PASS/FAIL terminal report with detailed KPIs.
"""

import sys
import os
import time
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.fault_analyzer import analyze_network_state
from topology.topo_graph import TopoGraph

# Terminal formatting
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

def print_banner():
    print(f"\n{BOLD}{CYAN}======================================================================")
    print("      🛡️  PATHGUARD AI-DRIVEN SELF-HEALING LIFECYCLE VERIFICATION  🛡️")
    print(f"======================================================================{RESET}")

def print_phase_header(phase_num, name, description):
    print(f"\n{BOLD}{BLUE}➤ PHASE {phase_num}: {name}{RESET}")
    print(f"  Description: {description}")
    print(f"  ----------------------------------------------------------------------")

def print_kpis(result):
    print(f"  📊  {BOLD}Telemetry KPIs:{RESET}")
    status = result["ai_status"]
    s_color = GREEN if status == "NORMAL" else (YELLOW if status == "WARNING" else RED)
    
    print(f"      • AI Severity Classification: {s_color}{status}{RESET}")
    print(f"      • Network Health Score:      {BOLD}{result['health_score']}/100{RESET}")
    print(f"      • Packet Loss:               {result['packet_loss_pct']:.1f}%")
    print(f"      • Average Latency:           {result['rtt_avg_ms']:.2f} ms")
    
    rec_status = result["recovery_status"]
    r_color = RED if rec_status == "RECOVERING" else (GREEN if "RECOVERED" in rec_status else CYAN)
    print(f"      • Self-Healing Status:       {BOLD}{r_color}{rec_status}{RESET}")
    
    if result["fault_analysis"]["failed_links"]:
        print(f"      • {RED}Failed Links Identified:   {', '.join([f['link'] for f in result['fault_analysis']['failed_links']])}{RESET}")
    if result["fault_analysis"]["degraded_links"]:
        print(f"      • {YELLOW}Degraded Links Identified: {', '.join([d['link'] for d in result['fault_analysis']['degraded_links']])}{RESET}")

def run_lifecycle_verification():
    topo = TopoGraph()
    test_results = []
    
    # ── PHASE 1: NORMAL ──────────────────────────────────────────────────
    print_phase_header(1, "NORMAL STATE", "Network is fully operational with low latency and 0% loss.")
    normal_df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T12:00:00Z",
            "source": "h1", "destination": "h6", "destination_ip": "10.0.2.1",
            "packets_sent": 3, "packets_received": 3, "packet_loss_pct": 0.0,
            "rtt_avg_ms": 6.5, "rtt_max_ms": 8.0, "rtt_mdev_ms": 0.4, "status": "ok"
        },
        {
            "timestamp": "2026-05-24T12:00:00Z",
            "source": "h6", "destination": "h11", "destination_ip": "10.0.3.1",
            "packets_sent": 3, "packets_received": 3, "packet_loss_pct": 0.0,
            "rtt_avg_ms": 7.2, "rtt_max_ms": 9.1, "rtt_mdev_ms": 0.3, "status": "ok"
        }
    ])
    res_1 = analyze_network_state(normal_df, topo)
    print_kpis(res_1)
    
    p1_pass = (res_1["ai_status"] == "NORMAL" and res_1["health_score"] >= 90 and res_1["recovery_status"] == "NORMAL")
    test_results.append(("Phase 1: NORMAL State Verification", p1_pass))
    print(f"  ➔ Result: {GREEN}PASS{RESET}" if p1_pass else f"  ➔ Result: {RED}FAIL{RESET}")

    # ── PHASE 2: WARNING ──────────────────────────────────────────────────
    print_phase_header(2, "WARNING STATE", "Access link s8-s4 experiences mild packet loss (jitter/instability).")
    # Spans h1 to h6 to cross switch boundaries so link analysis can correctly detect it
    warning_df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T12:01:00Z",
            "source": "h1", "destination": "h6", "destination_ip": "10.0.2.1",
            "packets_sent": 3, "packets_received": 3, "packet_loss_pct": 0.0,
            "rtt_avg_ms": 22.0, "rtt_max_ms": 45.0, "rtt_mdev_ms": 12.0, "status": "ok" # high mdev / jitter
        }
    ])
    res_2 = analyze_network_state(warning_df, topo)
    print_kpis(res_2)
    
    p2_pass = (res_2["ai_status"] == "WARNING" and 60 <= res_2["health_score"] < 90)
    test_results.append(("Phase 2: WARNING State Verification", p2_pass))
    print(f"  ➔ Result: {GREEN}PASS{RESET}" if p2_pass else f"  ➔ Result: {RED}FAIL{RESET}")

    # ── PHASE 3: CRITICAL ─────────────────────────────────────────────────
    print_phase_header(3, "CRITICAL STATE (Fault Detected)", "Backbone link s1-s2 fails completely, routing breaks.")
    critical_df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T12:02:00Z",
            "source": "h1", "destination": "h6", "destination_ip": "10.0.2.1",
            "packets_sent": 3, "packets_received": 0, "packet_loss_pct": 100.0,
            "rtt_avg_ms": 0.0, "rtt_max_ms": 0.0, "rtt_mdev_ms": 0.0, "status": "timeout"
        }
    ])
    res_3 = analyze_network_state(critical_df, topo)
    print_kpis(res_3)
    
    p3_pass = (res_3["ai_status"] == "CRITICAL" and res_3["health_score"] < 60)
    test_results.append(("Phase 3: CRITICAL State Verification", p3_pass))
    print(f"  ➔ Result: {GREEN}PASS{RESET}" if p3_pass else f"  ➔ Result: {RED}FAIL{RESET}")

    # ── PHASE 4: RECOVERING ───────────────────────────────────────────────
    print_phase_header(4, "RECOVERING STATE", "RecoveryEngine initiates dynamic reroute. REST API triggers POX rules.")
    # Simulate active recovery in database
    recovery_data_active = {
        "recovery_active": True,
        "last_recovery": {}
    }
    res_4 = analyze_network_state(critical_df, topo, recovery_data=recovery_data_active)
    print_kpis(res_4)
    
    p4_pass = (res_4["ai_status"] == "CRITICAL" and res_4["recovery_status"] == "RECOVERING")
    test_results.append(("Phase 4: RECOVERING State Verification", p4_pass))
    print(f"  ➔ Result: {GREEN}PASS{RESET}" if p4_pass else f"  ➔ Result: {RED}FAIL{RESET}")

    # ── PHASE 5: RECOVERED ────────────────────────────────────────────────
    print_phase_header(5, "RECOVERED STATE", "OpenFlow bypass rules installed. Traffic flows cleanly via alternate Path_2.")
    # Telemetry is stable/healthy again, but recovery path is active
    recovered_df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T12:03:00Z",
            "source": "h1", "destination": "h6", "destination_ip": "10.0.2.1",
            "packets_sent": 3, "packets_received": 3, "packet_loss_pct": 0.0,
            "rtt_avg_ms": 11.5, "rtt_max_ms": 14.0, "rtt_mdev_ms": 0.5, "status": "ok"
        }
    ])
    recovery_data_completed = {
        "recovery_active": False,
        "last_recovery": {
            "timestamp": "2026-05-24T12:03:00Z",
            "status": "SUCCESS",
            "failed_link": "s1-s2",
            "selected_path": "Path_2",
            "route": "s8 → s5 → s1 → s3 → s6 → s9",
            "duration_sec": 0.38
        }
    }
    res_5 = analyze_network_state(recovered_df, topo, recovery_data=recovery_data_completed)
    print_kpis(res_5)
    
    p5_pass = (res_5["ai_status"] == "NORMAL" and "RECOVERED" in res_5["recovery_status"])
    test_results.append(("Phase 5: RECOVERED State Verification", p5_pass))
    print(f"  ➔ Result: {GREEN}PASS{RESET}" if p5_pass else f"  ➔ Result: {RED}FAIL{RESET}")

    # ── PHASE 6: NORMAL (RESTORED) ───────────────────────────────────────
    print_phase_header(6, "NORMAL STATE (Restored)", "Failed backbone link is physically restored. SDN reset to full-mesh.")
    # No recovery data active and link status completely normal
    res_6 = analyze_network_state(recovered_df, topo)
    print_kpis(res_6)
    
    p6_pass = (res_6["ai_status"] == "NORMAL" and res_6["recovery_status"] == "NORMAL")
    test_results.append(("Phase 6: Restored NORMAL State Verification", p6_pass))
    print(f"  ➔ Result: {GREEN}PASS{RESET}" if p6_pass else f"  ➔ Result: {RED}FAIL{RESET}")

    # ── SUMMARY REPORT ────────────────────────────────────────────────────
    print(f"\n{BOLD}{CYAN}======================================================================")
    print("                      LIFECYCLE SUMMARY REPORT")
    print(f"======================================================================{RESET}")
    all_passed = True
    for test_name, passed in test_results:
        mark = f"{GREEN}✓ PASS{RESET}" if passed else f"{RED}✗ FAIL{RESET}"
        print(f"  • {test_name:<45s} : {mark}")
        if not passed:
            all_passed = False
            
    print(f"  ----------------------------------------------------------------------")
    final_status = f"{GREEN}{BOLD}ALL TESTS PASSED{RESET}" if all_passed else f"{RED}{BOLD}SOME TESTS FAILED{RESET}"
    print(f"  🏁  {BOLD}Final Verification Result: {final_status}")
    print(f"{CYAN}======================================================================{RESET}\n")
    
    return all_passed

if __name__ == "__main__":
    print_banner()
    success = run_lifecycle_verification()
    sys.exit(0 if success else 1)
