#!/usr/bin/env python3
"""
PathGuard — Automated Data Collection Script
=============================================

Collects network monitoring data by:
  1. Building the PathGuard topology
  2. Running normal-state monitoring rounds
  3. Simulating link failures and collecting fault data
  4. Saving everything to datasets/network_data.csv

This avoids the concurrent-access crash that happens when
monitoring and pingall run at the same time in the CLI.

Usage
-----
  sudo python3 collect_data.py
  sudo python3 collect_data.py --rounds 10 --interval 3
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI

from topology.topology import PathGuardTopo, cleanup_mininet
from monitoring.monitor import (
    NetworkMonitor, collect_once, CSVWriter, log_record
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="PathGuard — Automated data collection"
    )
    parser.add_argument(
        "--rounds", type=int, default=5,
        help="Number of normal monitoring rounds (default: 5)"
    )
    parser.add_argument(
        "--fault-rounds", type=int, default=3,
        help="Number of monitoring rounds per fault scenario (default: 3)"
    )
    parser.add_argument(
        "--interval", type=float, default=3,
        help="Seconds between rounds (default: 3)"
    )
    parser.add_argument(
        "--csv", default="datasets/network_data.csv",
        help="Output CSV path (default: datasets/network_data.csv)"
    )
    parser.add_argument(
        "--controller-ip", default="127.0.0.1",
        help="SDN controller IP (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--controller-port", type=int, default=6633,
        help="SDN controller port (default: 6633)"
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="Drop into Mininet CLI after data collection"
    )
    return parser.parse_args()


def collect_rounds(net, csv_writer, num_rounds, interval, label=""):
    """Collect multiple rounds of monitoring data sequentially."""
    for r in range(1, num_rounds + 1):
        ts = time.strftime("%H:%M:%S")
        print(f"\n{'─' * 60}")
        print(f"  📡  {label} Round {r}/{num_rounds}  —  {ts}")
        print(f"{'─' * 60}")

        records = collect_once(net, ping_count=4, ping_timeout=10)
        csv_writer.write_many(records)

        for rec in records:
            log_record(rec)

        # Summary
        total = len(records)
        healthy = sum(1 for r in records if r.status == "ok")
        failed = total - healthy
        print(f"\n  Summary: {healthy}/{total} healthy, {failed} faults")

        if r < num_rounds:
            time.sleep(interval)


def main():
    args = parse_args()
    setLogLevel("info")

    # ── Cleanup and build ────────────────────────────────────────
    cleanup_mininet()

    info("*** Creating PathGuard topology for data collection\n")
    topo = PathGuardTopo()
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(
            name, ip=args.controller_ip, port=args.controller_port
        ),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=True,
    )

    info("*** Starting network\n")
    net.start()

    info("*** Waiting for controller discovery and spanning tree...\n")
    time.sleep(5)

    # ── Verify connectivity ──────────────────────────────────────
    info("*** Verifying connectivity\n")
    net.pingAll()

    # ── Setup CSV writer ─────────────────────────────────────────
    csv_writer = CSVWriter(args.csv)

    print("\n" + "=" * 60)
    print("  🧪  PathGuard — Automated Data Collection")
    print("=" * 60)

    # ── Phase 1: Normal traffic ──────────────────────────────────
    print("\n\n" + "=" * 60)
    print("  📊  PHASE 1: Collecting NORMAL traffic data")
    print("=" * 60)

    collect_rounds(net, csv_writer, args.rounds, args.interval,
                   label="NORMAL")

    # ── Phase 2: Link failure scenarios ──────────────────────────
    fault_scenarios = [
        ("s1", "s2", "Link s1-s2 down"),
        ("s2", "s3", "Link s2-s3 down"),
        ("s1", "s3", "Link s1-s3 down"),
    ]

    for node1, node2, desc in fault_scenarios:
        print("\n\n" + "=" * 60)
        print(f"  ⚠️  PHASE 2: Simulating FAULT — {desc}")
        print("=" * 60)

        # Bring link down
        info(f"*** Taking down link {node1} <-> {node2}\n")
        net.configLinkStatus(node1, node2, "down")
        time.sleep(2)  # let the network react

        # Collect fault data
        collect_rounds(net, csv_writer, args.fault_rounds, args.interval,
                       label=f"FAULT ({desc})")

        # Restore link
        info(f"*** Restoring link {node1} <-> {node2}\n")
        net.configLinkStatus(node1, node2, "up")
        time.sleep(2)  # let the network recover

        # Collect recovery data
        print(f"\n  🔄  Collecting recovery data after {desc} restored...")
        collect_rounds(net, csv_writer, 2, args.interval,
                       label=f"RECOVERY ({desc})")

    # ── Done ─────────────────────────────────────────────────────
    csv_writer.close()

    print("\n\n" + "=" * 60)
    print("  ✅  Data Collection Complete!")
    print("=" * 60)

    # Count rows in CSV
    csv_path = Path(args.csv)
    if csv_path.exists():
        with open(csv_path) as f:
            row_count = sum(1 for _ in f) - 1  # subtract header
        print(f"\n  CSV:   {csv_path}")
        print(f"  Rows:  {row_count}")
    print(f"\n  Next step: python3 ai/train_model.py\n")

    # ── Optional CLI ─────────────────────────────────────────────
    if args.cli:
        info("*** Entering Mininet CLI\n")
        CLI(net)

    # ── Cleanup ──────────────────────────────────────────────────
    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    main()
