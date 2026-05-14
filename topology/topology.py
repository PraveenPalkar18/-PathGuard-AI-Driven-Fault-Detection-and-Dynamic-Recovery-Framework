#!/usr/bin/env python3
r"""
PathGuard: AI-Driven Fault Detection and Dynamic Recovery Framework
====================================================================

Mininet-WiFi / Mininet SDN Topology
------------------------------------
This script builds a custom SDN topology with:
  - 1  remote OpenFlow controller  (default: POX on 127.0.0.1:6633)
  - 3  OpenFlow switches           (s1, s2, s3)
  - 4  hosts                       (h1 ... h4)
  - Multiple redundant paths between switches for failover experiments

Network Diagram
---------------

           +---- s1 ----+
           |  /      \   |
     h1 ---+            +--- h3
     h2 ---+  s2 -- s3  +--- h4
           |  \      /  |
           +-------------+

  Links (with configurable bandwidth / delay):
      s1 <-> s2   (primary path)
      s2 <-> s3   (primary path)
      s1 <-> s3   (redundant / backup path)
      h1 <-> s1,  h2 <-> s1
      h3 <-> s3,  h4 <-> s2

  All switch-to-switch links form a full-mesh triangle so that
  any single link failure still leaves an alternative path.

Usage
-----
  sudo python3 topology/topology.py [--controller-ip IP] [--controller-port PORT]

Requires
--------
  - Mininet  (apt install mininet)
  - Open vSwitch  (apt install openvswitch-switch)
  - Optional: mininet-wifi  (pip install mininet-wifi)
  - POX SDN controller (~/pox/pox.py forwarding.l2_learning)
"""

import argparse
import os
import subprocess
import sys
import time

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
    Custom topology for PathGuard fault-detection experiments.

    Configurable parameters (passed via **opts or defaults):
        bw_host      – bandwidth on host↔switch links  (Mbit/s, default 100)
        bw_switch    – bandwidth on switch↔switch links (Mbit/s, default 100)
        delay_host   – propagation delay on host links  (default '1ms')
        delay_switch – propagation delay on switch links(default '5ms')
        loss_switch  – packet-loss % on switch links    (default 0)
        max_queue    – max queue size (packets)          (default 1000)
    """

    def build(self, **opts):
        # ── Tuneable link parameters ──────────────────────────────────
        bw_host      = opts.get("bw_host",      100)     # Mbit/s
        bw_switch    = opts.get("bw_switch",     100)     # Mbit/s
        delay_host   = opts.get("delay_host",    "1ms")
        delay_switch = opts.get("delay_switch",  "5ms")
        loss_switch  = opts.get("loss_switch",   0)       # percent
        max_queue    = opts.get("max_queue",      1000)

        # Link-option dictionaries for readability
        host_link_opts = dict(
            bw=bw_host,
            delay=delay_host,
            max_queue_size=max_queue,
        )
        switch_link_opts = dict(
            bw=bw_switch,
            delay=delay_switch,
            loss=loss_switch,
            max_queue_size=max_queue,
        )

        # ── Switches (OpenFlow 1.0 — compatible with POX) ─────────────
        # Three OVS switches form a fully-connected triangle.
        # Using OpenFlow10 for POX controller compatibility.
        s1 = self.addSwitch("s1", protocols="OpenFlow10")
        s2 = self.addSwitch("s2", protocols="OpenFlow10")
        s3 = self.addSwitch("s3", protocols="OpenFlow10")

        # ── Hosts ─────────────────────────────────────────────────────
        h1 = self.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
        h2 = self.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")
        h3 = self.addHost("h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03")
        h4 = self.addHost("h4", ip="10.0.0.4/24", mac="00:00:00:00:00:04")

        # ── Host ↔ Switch links ──────────────────────────────────────
        # h1 and h2 attach to switch s1 (left-hand side of the topology)
        self.addLink(h1, s1, **host_link_opts)
        self.addLink(h2, s1, **host_link_opts)

        # h3 attaches to switch s3 (right-hand side)
        self.addLink(h3, s3, **host_link_opts)

        # h4 attaches to switch s2 (bottom)
        self.addLink(h4, s2, **host_link_opts)

        # ── Switch ↔ Switch links (full mesh — 3 links) ─────────────
        # These redundant paths are the core of PathGuard's
        # rerouting capability.  Taking any one link down still
        # leaves an alternative path between every pair of switches.

        # Primary path:  s1 ↔ s2
        self.addLink(s1, s2, **switch_link_opts)

        # Primary path:  s2 ↔ s3
        self.addLink(s2, s3, **switch_link_opts)

        # Redundant / backup path:  s1 ↔ s3
        self.addLink(s1, s3, **switch_link_opts)


# ──────────────────────────────────────────────────────────────────────
# 2.  NETWORK STARTUP HELPERS
# ──────────────────────────────────────────────────────────────────────

def parse_args():
    """Parse command-line arguments for controller address & link tuning."""
    parser = argparse.ArgumentParser(
        description="PathGuard SDN topology (Mininet)"
    )
    parser.add_argument(
        "--controller-ip", default="127.0.0.1",
        help="IP address of the remote SDN controller (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--controller-port", type=int, default=6633,
        help="TCP port of the remote SDN controller (default: 6633 for POX)"
    )
    parser.add_argument(
        "--bw-host", type=int, default=100,
        help="Bandwidth for host links in Mbit/s (default: 100)"
    )
    parser.add_argument(
        "--bw-switch", type=int, default=100,
        help="Bandwidth for switch-switch links in Mbit/s (default: 100)"
    )
    parser.add_argument(
        "--delay-host", default="1ms",
        help="Propagation delay for host links (default: 1ms)"
    )
    parser.add_argument(
        "--delay-switch", default="5ms",
        help="Propagation delay for switch links (default: 5ms)"
    )
    parser.add_argument(
        "--loss", type=float, default=0,
        help="Packet loss %% on switch-switch links (default: 0)"
    )
    parser.add_argument(
        "--monitor", action="store_true",
        help="Start network monitoring automatically (writes to datasets/network_data.csv)"
    )
    parser.add_argument(
        "--monitor-interval", type=float, default=5,
        help="Monitoring interval in seconds (default: 5)"
    )
    return parser.parse_args()


def print_topology_info(net):
    """Print a summary of the running topology."""
    info("\n")
    info("=" * 62 + "\n")
    info("  PathGuard Topology — Running\n")
    info("=" * 62 + "\n\n")

    info("  Hosts:\n")
    for host in net.hosts:
        info(f"    {host.name:4s}  IP={host.IP():15s}  MAC={host.MAC()}\n")

    info("\n  Switches:\n")
    for switch in net.switches:
        info(f"    {switch.name:4s}  DPID={switch.dpid}\n")

    info("\n  Links:\n")
    for link in net.links:
        info(f"    {link.intf1} <---> {link.intf2}\n")

    info("\n" + "=" * 62 + "\n")
    info("  Useful Mininet CLI commands for PathGuard experiments:\n")
    info("-" * 62 + "\n")
    info("  pingall                       – verify full connectivity\n")
    info("  h1 ping -c 4 h3              – test specific path\n")
    info("  link s1 s2 down              – simulate link failure\n")
    info("  link s1 s2 up                – restore link\n")
    info("  sh ovs-ofctl dump-flows s1   – inspect flow table\n")
    info("  iperf h1 h3                  – bandwidth test\n")
    info("  h1 traceroute h3             – trace route through switches\n")
    info("=" * 62 + "\n\n")


# ──────────────────────────────────────────────────────────────────────
# 3.  LINK FAILURE SIMULATION UTILITIES
#     (callable from the CLI or imported by other PathGuard modules)
# ──────────────────────────────────────────────────────────────────────

def simulate_link_failure(net, node1_name, node2_name):
    """
    Bring down the link between two nodes.

    Example (from Mininet CLI):
        py simulate_link_failure(net, 's1', 's2')

    Or from the CLI directly:
        link s1 s2 down
    """
    info(f"*** Simulating link failure: {node1_name} <-> {node2_name}\n")
    net.configLinkStatus(node1_name, node2_name, "down")


def restore_link(net, node1_name, node2_name):
    """
    Bring a previously failed link back up.

    Example:
        py restore_link(net, 's1', 's2')
    """
    info(f"*** Restoring link: {node1_name} <-> {node2_name}\n")
    net.configLinkStatus(node1_name, node2_name, "up")


def latency_probe(net, src_name, dst_name, count=4):
    """
    Send ICMP pings and return the output for latency monitoring.

    Example:
        py latency_probe(net, 'h1', 'h3')
    """
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
    """Clean up any leftover Mininet state to avoid RTNETLINK errors."""
    info("*** Cleaning up previous Mininet state\n")
    # Suppress output — this is just housekeeping
    subprocess.run(
        ["mn", "-c"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run():
    """Build and launch the PathGuard topology."""
    args = parse_args()
    setLogLevel("info")

    # Clean up stale interfaces from previous runs
    cleanup_mininet()

    info("*** Creating PathGuard topology\n")

    # Build topology with user-specified link parameters
    topo = PathGuardTopo(
        bw_host=args.bw_host,
        bw_switch=args.bw_switch,
        delay_host=args.delay_host,
        delay_switch=args.delay_switch,
        loss_switch=args.loss,
    )

    # Instantiate the network with a remote SDN controller
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(
            name,
            ip=args.controller_ip,
            port=args.controller_port,
        ),
        switch=OVSKernelSwitch,
        link=TCLink,            # traffic-control links (supports bw/delay/loss)
        autoSetMacs=False,      # we set MACs explicitly above
        autoStaticArp=True,     # pre-populate ARP tables for cleaner tests
    )

    info("*** Starting network\n")
    net.start()

    # Brief pause to let the controller discover the topology
    # and for spanning tree to converge (important for loop-free forwarding)
    info("*** Waiting for controller discovery and spanning tree...\n")
    time.sleep(5)

    # Print topology summary and helpful commands
    print_topology_info(net)

    # ── Start monitoring if requested ────────────────────────────
    monitor = None
    if args.monitor:
        # Add project root to path so we can import monitoring module
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, project_root)
        from monitoring.monitor import NetworkMonitor

        # Try to load AI model for real-time fault detection
        fault_detector = None
        model_path = os.path.join(project_root, "ai", "model.pkl")
        if os.path.exists(model_path):
            try:
                from ai.train_model import FaultDetector
                fault_detector = FaultDetector.load(model_path)
                info("*** AI model loaded from %s\n" % model_path)
            except Exception as exc:
                info("*** Could not load AI model: %s\n" % exc)

        monitor = NetworkMonitor(
            net, interval=args.monitor_interval,
            fault_detector=fault_detector,
        )
        monitor.start()
        info("*** Monitoring started (interval=%ss, csv=datasets/network_data.csv)\n" % args.monitor_interval)

    # ── Interactive CLI or Blocking Loop ───────────────────────────
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

    # ── Cleanup ───────────────────────────────────────────────────
    if monitor:
        info("*** Stopping monitor\n")
        monitor.stop()
    info("*** Stopping network\n")
    net.stop()


# ──────────────────────────────────────────────────────────────────────
# 5.  ENTRY POINT
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
