#!/usr/bin/env python3
"""
PathGuard: Automated Final Exam Demo Orchestrator
=================================================
Programmatically boots the 12-switch topology, starts the background monitor,
and executes all 6 transition phases sequentially:
  NORMAL ➔ WARNING ➔ CRITICAL ➔ RECOVERING ➔ RECOVERED ➔ Restored NORMAL

Generates realistic iperf/ping background traffic, delay/jitter congestion,
core link failure, and lets the self-healing recovery engine trigger automatically.
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
import warnings
import random
warnings.filterwarnings("ignore")
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info

from topology.topology import PathGuardTopo, cleanup_mininet
from monitoring.monitor import NetworkMonitor
from ai.train_model import FaultDetector
from recovery.recover import log_event

# Terminal formatting
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

def print_phase_banner(phase_num: int, title: str, desc: str):
    print(f"\n{BOLD}{CYAN}" + "=" * 70)
    print(f" [PHASE {phase_num}] {title.upper()}".center(70))
    print("=" * 70 + f"{RESET}")
    print(f"  {BOLD}Objective:{RESET} {desc}")
    print(f"  {BOLD}Dashboard Expectation:{RESET} State transitions to {BOLD}{title}{RESET}\n")

def main():
    setLogLevel("info")
    
    # ── Cleanup ──────────────────────────────────────────────────────────
    print(f"{BOLD}{BLUE}➔ Preparing SDN environment...{RESET}")
    os.system("killall iperf 2>/dev/null || true")
    cleanup_mininet()
    
    # ── Initialize Mininet Network ──────────────────────────────────────
    print(f"{BOLD}{BLUE}➔ Building 12-switch PathGuard Network...{RESET}")
    topo = PathGuardTopo()
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(
            name, ip="127.0.0.1", port=6633
        ),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=True,
    )
    
    print(f"{BOLD}{BLUE}➔ Starting network and waiting for controller discovery...{RESET}")
    net.start()
    time.sleep(5)  # Wait for switches to connect to POX
    
    # ── Load Hardened AI Model ──────────────────────────────────────────
    fault_detector = None
    model_path = project_root / "ai" / "model.pkl"
    if model_path.exists():
        try:
            fault_detector = FaultDetector.load(str(model_path))
            print(f"{GREEN}✓ AI model loaded successfully from {model_path}{RESET}")
        except Exception as e:
            print(f"{RED}⚠ Could not load AI model: {e}{RESET}")
    else:
        print(f"{RED}⚠ Warning: Model file not found. Fallback mode active.{RESET}")
        
    # ── Start Telemetry Monitor in Background Thread ────────────────────
    monitor = NetworkMonitor(
        net=net,
        csv_path=project_root / "datasets" / "network_data.csv",
        interval=1.5,
        fault_detector=fault_detector,
        monitored_hosts=["h1", "h6", "h11", "h16", "h21", "h24"]
    )
    
    print(f"{BOLD}{BLUE}➔ Booting background telemetry monitoring loop...{RESET}")
    monitor.start()
    time.sleep(2)  # Let first round collect
    
    try:
        # Get representative nodes for traffic generation
        h1 = net.get("h1")
        h6 = net.get("h6")
        h11 = net.get("h11")
        h21 = net.get("h21")
        
        # ──────────────────────────────────────────────────────────────────
        # PHASE 1: NORMAL STATE
        # ──────────────────────────────────────────────────────────────────
        print_phase_banner(
            1, "NORMAL",
            "Generate normal baseline traffic. Verify 0% packet loss and low latency."
        )
        log_event("[DEMO] Starting Phase 1: NORMAL STATE", "INFO")
        
        # Launch light background TCP traffic
        print(f"  ⚡ Starting light background TCP flows (h1 ➔ h6)...")
        h6.cmd("iperf -s -p 5001 -D")
        h1.popen(["iperf", "-c", h6.IP(), "-p", "5001", "-t", "90", "-b", "1M"])
        
        # Launch background ping flow
        print(f"  ⚡ Running background pings (h11 ➔ h21)...")
        h11.popen(["ping", "-c", "40", "-i", "1", h21.IP()])
        
        print(f"  ⏱️  Running Phase 1 for 20 seconds...")
        time.sleep(20)
        
        # ──────────────────────────────────────────────────────────────────
        # PHASE 2: WARNING STATE (CONGESTION)
        # ──────────────────────────────────────────────────────────────────
        print_phase_banner(
            2, "WARNING",
            "Inject network delay and packet loss on access segment s4-s8. Saturate links."
        )
        log_event("[DEMO] Starting Phase 2: WARNING STATE (Degrading link s4-s8)", "WARNING")
        
        # Inject link degradation on switch interfaces between s4 and s8
        print(f"  💥 Injecting link degradation on access segment s4-s8 (10% loss, 35ms delay)...")
        s4 = net.get("s4")
        s8 = net.get("s8")
        for intf1, intf2 in s4.connectionsTo(s8):
            intf1.config(loss=10, delay="35ms")
            intf2.config(loss=10, delay="35ms")
            
        # Start heavy iperf flows to saturate link and generate extreme jitter
        print(f"  ⚡ Launching heavy parallel congestion flows (h1 ➔ h6)...")
        h1.popen(["iperf", "-c", h6.IP(), "-p", "5001", "-t", "40", "-P", "4"])
        
        print(f"  ⏱️  Running Phase 2 for 20 seconds...")
        time.sleep(20)
        
        # ──────────────────────────────────────────────────────────────────
        # PHASE 3: CRITICAL STATE (LINK FAILURE)
        # ──────────────────────────────────────────────────────────────────
        print_phase_banner(
            3, "CRITICAL",
            "Simulate complete core-distribution link failure on segment s4-s8."
        )
        log_event("[DEMO] Starting Phase 3: CRITICAL STATE (Cutting link s4-s8 down)", "CRITICAL")
        
        # Bring the link completely down
        print(f"  ❌ Cutting link s4-s8 completely DOWN...")
        net.configLinkStatus("s4", "s8", "down")
        
        # Start pinging to confirm packet loss spikes to 100%
        h1.popen(["ping", "-c", "10", "-W", "1", "10.0.2.1"])
        
        print(f"  ⏱️  Running Phase 3 for 20 seconds...")
        time.sleep(20)
        
        # ──────────────────────────────────────────────────────────────────
        # PHASE 4: RECOVERING STATE
        # ──────────────────────────────────────────────────────────────────
        print_phase_banner(
            4, "RECOVERING",
            "Telemetry monitor detects failure with high confidence, launching RecoveryEngine."
        )
        # Recovery triggers automatically inside NetworkMonitor background loop!
        # The dashboard will transition into "RECOVERING" while pushing SDN flow tables.
        
        print(f"  ⏱️  Running Phase 4 for 20 seconds...")
        time.sleep(20)
        
        # ──────────────────────────────────────────────────────────────────
        # PHASE 5: RECOVERED STATE
        # ──────────────────────────────────────────────────────────────────
        print_phase_banner(
            5, "RECOVERED",
            "Bypass rules installed on OpenFlow switches. Ping verification restored."
        )
        # Alternate path will now carry traffic cleanly.
        
        print(f"  ⏱️  Running Phase 5 for 20 seconds...")
        time.sleep(20)
        
        # ──────────────────────────────────────────────────────────────────
        # PHASE 6: RESTORED NORMAL STATE
        # ──────────────────────────────────────────────────────────────────
        print_phase_banner(
            6, "RESTORED NORMAL",
            "Physically restore failed link, terminate congestion, and reset POX to baseline."
        )
        log_event("[DEMO] Starting Phase 6: RESTORED NORMAL", "RESTORED")
        
        # Bring link s4-s8 back UP and reset TC properties
        print(f"  🔌 Restoring link s4-s8 to physical UP state...")
        net.configLinkStatus("s4", "s8", "up")
        time.sleep(1)
        s4 = net.get("s4")
        s8 = net.get("s8")
        for intf1, intf2 in s4.connectionsTo(s8):
            intf1.config(loss=0, delay="2ms")
            intf2.config(loss=0, delay="2ms")
            
        # Clear iperf instances
        print(f"  ⚡ Terminating background TCP congestion flows...")
        os.system("killall iperf 2>/dev/null || true")
        
        # Reset recovery rules back to normal full-mesh baseline
        print(f"  🔄 Instructing POX to restore full-mesh NORMAL flow tables...")
        monitor.recovery_engine.reset_to_normal()
        
        print(f"  ⏱️  Running Phase 6 for 20 seconds...")
        time.sleep(20)
        
    except KeyboardInterrupt:
        print(f"\n{YELLOW}⚠ Demo interrupted by user.{RESET}")
    finally:
        # ── Graceful Shutdown ────────────────────────────────────────────
        print(f"\n{BOLD}{BLUE}➔ Shutting down automated demo and cleaning up network...{RESET}")
        os.system("killall iperf 2>/dev/null || true")
        monitor.stop()
        net.stop()
        cleanup_mininet()
        print(f"{GREEN}✓ SDN environments cleaned up. Demo complete!{RESET}\n")

if __name__ == "__main__":
    main()
