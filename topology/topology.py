#!/usr/bin/env python3
r"""
PathGuard: AI-Driven Fault Detection and Dynamic Recovery Framework
====================================================================

Mininet-WiFi / Mininet SDN Topology
------------------------------------
This script builds a custom SDN topology with:
  - 1  remote OpenFlow controller  (default: POX on 127.0.0.1:6633)
  - 12 OpenFlow switches           (s1 ... s12)
  - 24 hosts                       (h1 ... h24)
  - Hierarchical + Mesh hybrid design

Usage
-----
  sudo python3 topology/topology.py [--controller-ip IP] [--controller-port PORT]

Requires
--------
  - Mininet  (apt install mininet)
  - Open vSwitch  (apt install openvswitch-switch)
"""

import argparse
import os
import subprocess
import sys
import time
import json

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.topo import Topo


# ──────────────────────────────────────────────────────────────────────
# 1.  TOPOLOGY DEFINITION
# ──────────────────────────────────────────────────────────────────────

class PathGuardTopo(Topo):
    """
    Custom 12-switch topology for PathGuard fault-detection experiments.
    """

    def build(self, **opts):
        # Load from port_map.json
        port_map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "port_map.json")
        with open(port_map_path, "r") as f:
            topo_data = json.load(f)

        # ── Switches (OpenFlow 1.0 — compatible with POX) ─────────────
        for sw_name in topo_data["switches"].keys():
            self.addSwitch(sw_name, protocols="OpenFlow10")

        # ── Hosts ─────────────────────────────────────────────────────
        for h_name, h_info in topo_data["hosts"].items():
            self.addHost(h_name, ip=f"{h_info['ip']}/8", mac=h_info["mac"])

        # ── Switch ↔ Switch links ─────────────────────────────────────
        # Configure parameters based on link type
        bw_core = opts.get("bw_core", 100)
        bw_dist = opts.get("bw_dist", 50)
        bw_access = opts.get("bw_access", 20)
        loss_switch = opts.get("loss_switch", 0)

        for link in topo_data["links"]:
            bw = link.get("bw", 100)
            if link["type"] == "core":
                bw = bw_core
            elif link["type"] == "dist":
                bw = bw_dist
            elif link["type"] == "access":
                bw = bw_access
                
            self.addLink(
                link["src"], link["dst"],
                bw=bw,
                delay=link["delay"],
                loss=loss_switch,
                max_queue_size=1000
            )

        # ── Host ↔ Switch links ──────────────────────────────────────
        bw_host = opts.get("bw_host", 10)
        for h_name, h_info in topo_data["hosts"].items():
            self.addLink(
                h_name, h_info["switch"],
                bw=bw_host,
                delay="1ms",
                max_queue_size=1000,
                port2=h_info["port"]  # Enforce specific port on switch side
            )


# ──────────────────────────────────────────────────────────────────────
# 2.  NETWORK STARTUP HELPERS
# ──────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="PathGuard SDN topology (Mininet)")
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6633)
    parser.add_argument("--bw-core", type=int, default=100)
    parser.add_argument("--bw-dist", type=int, default=50)
    parser.add_argument("--bw-access", type=int, default=20)
    parser.add_argument("--bw-host", type=int, default=10)
    parser.add_argument("--loss", type=float, default=0)
    parser.add_argument("--monitor", action="store_true")
    parser.add_argument("--monitor-interval", type=float, default=5)
    return parser.parse_args()


def print_topology_info(net):
    info("\n")
    info("=" * 62 + "\n")
    info("  PathGuard Enterprise Topology — Running\n")
    info("=" * 62 + "\n\n")

    info("  Hosts (24 total):\n")
    info("    h1-h5:   10.0.1.X  (a1/s8)\n")
    info("    h6-h10:  10.0.2.X  (a2/s9)\n")
    info("    h11-h15: 10.0.3.X  (a3/s10)\n")
    info("    h16-h20: 10.0.4.X  (a4/s11)\n")
    info("    h21-h24: 10.0.5.X  (a5/s12)\n")

    info("\n  Switches (12 total):\n")
    info("    Core: c1-c3 (s1-s3)\n")
    info("    Dist: d1-d4 (s4-s7)\n")
    info("    Acc:  a1-a5 (s8-s12)\n")

    info("\n" + "=" * 62 + "\n")


# ──────────────────────────────────────────────────────────────────────
# 3.  LINK FAILURE SIMULATION UTILITIES
# ──────────────────────────────────────────────────────────────────────

def simulate_link_failure(net, node1_name, node2_name):
    info(f"*** Simulating link failure: {node1_name} <-> {node2_name}\n")
    net.configLinkStatus(node1_name, node2_name, "down")

def restore_link(net, node1_name, node2_name):
    info(f"*** Restoring link: {node1_name} <-> {node2_name}\n")
    net.configLinkStatus(node1_name, node2_name, "up")

def latency_probe(net, src_name, dst_name, count=4):
    src = net.get(src_name)
    dst = net.get(dst_name)
    info(f"*** Latency probe: {src_name} -> {dst_name} ({count} pings)\n")
    result = src.cmd(f"ping -c {count} {dst.IP()}")
    info(result + "\n")
    return result


# ──────────────────────────────────────────────────────────────────────
# 4.  MAIN — build, start, and drop into the Mininet CLI
# ──────────────────────────────────────────────────────────────────────

def cleanup_mininet():
    info("*** Cleaning up previous Mininet state\n")
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Forcefully delete any remaining OVS bridges to prevent "File exists" errors
    try:
        res = subprocess.run(["ovs-vsctl", "list-br"], capture_output=True, text=True)
        if res.returncode == 0:
            for br in res.stdout.splitlines():
                if br.strip():
                    subprocess.run(["ovs-vsctl", "--if-exists", "del-br", br.strip()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def run():
    args = parse_args()
    setLogLevel("info")
    cleanup_mininet()

    info("*** Creating PathGuard topology\n")
    topo = PathGuardTopo(
        bw_core=args.bw_core,
        bw_dist=args.bw_dist,
        bw_access=args.bw_access,
        bw_host=args.bw_host,
        loss_switch=args.loss,
    )

    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip=args.controller_ip, port=args.controller_port),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=True,
    )

    info("*** Starting network\n")
    net.start()

    info("*** Waiting for controller discovery...\n")
    time.sleep(5)

    print_topology_info(net)

    monitor = None
    if args.monitor:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, project_root)
        from monitoring.monitor import NetworkMonitor

        fault_detector = None
        model_path = os.path.join(project_root, "ai", "model.pkl")
        if os.path.exists(model_path):
            try:
                from ai.train_model import FaultDetector
                fault_detector = FaultDetector.load(model_path)
                info("*** AI model loaded from %s\n" % model_path)
            except Exception as exc:
                info("*** Could not load AI model: %s\n" % exc)

        monitor = NetworkMonitor(net, interval=args.monitor_interval, fault_detector=fault_detector)
        monitor.start()
        info(f"*** Monitoring started (interval={args.monitor_interval}s, csv=datasets/network_data.csv)\n")

    if not args.monitor:
        info("*** Entering Mininet CLI — type 'exit' or Ctrl-D to quit\n")
        CLI(net)
    else:
        info("*** Headless Monitoring Mode Active — press Ctrl-C to terminate topology\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            info("\n*** Interrupted by user. Shutting down...\n")

    if monitor:
        info("*** Stopping monitor\n")
        monitor.stop()
    info("*** Stopping network\n")
    net.stop()

if __name__ == "__main__":
    run()
