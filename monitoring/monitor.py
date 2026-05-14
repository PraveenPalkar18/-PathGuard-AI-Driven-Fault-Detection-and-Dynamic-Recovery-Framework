#!/usr/bin/env python3
"""
PathGuard: AI-Driven Fault Detection and Dynamic Recovery Framework
====================================================================

Network Health Monitor
----------------------
Continuously probes every host-pair in a running Mininet topology and
records latency, packet-loss, and metadata to a CSV file that is ready
for downstream ML / AI fault-detection pipelines.

Data Flow
---------
  Mininet hosts ──► ICMP ping ──► raw stdout ──► parse_ping()
        ──► MonitorRecord (dataclass) ──► CSV append  +  terminal log

Output CSV columns
------------------
  timestamp, source, destination, packets_sent, packets_received,
  packet_loss_pct, rtt_min_ms, rtt_avg_ms, rtt_max_ms, rtt_mdev_ms,
  status

Usage
-----
  # Standalone (connects to the running Mininet instance via its API):
  sudo python3 monitoring/monitor.py

  # Integrated — called from within the topology script or Mininet CLI:
  from monitoring.monitor import NetworkMonitor
  mon = NetworkMonitor(net, interval=5)
  mon.start()          # runs in a background thread
  ...
  mon.stop()

  # Or one-shot collection for a notebook / test:
  from monitoring.monitor import collect_once
  records = collect_once(net)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from dataclasses import dataclass, fields, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from collections import deque, defaultdict

# Add parent project root to path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from monitoring.health import calculate_health_score, get_health_label
except ImportError:
    def calculate_health_score(avg_latency, max_loss, active_failures=0, instability_count=0): return 100
    def get_health_label(score): return "Healthy"

from recovery.recover import RecoveryEngine, log_event

# ──────────────────────────────────────────────────────────────────────
# 1.  DATA MODEL
#     A single probe result.  Using a dataclass keeps the schema
#     explicit — the same field names become the CSV header and, later,
#     the feature vector for the ML model.
# ──────────────────────────────────────────────────────────────────────

@dataclass
class MonitorRecord:
    """One ping-probe measurement between a source and destination host."""
    timestamp:          str     # ISO-8601 UTC timestamp
    source:             str     # Mininet host name  (e.g. "h1")
    destination:        str     # Mininet host name  (e.g. "h3")
    destination_ip:     str     # IP address pinged
    packets_sent:       int
    packets_received:   int
    packet_loss_pct:    float   # 0.0 – 100.0
    rtt_min_ms:         float   # round-trip time min   (ms)
    rtt_avg_ms:         float   # round-trip time avg   (ms)
    rtt_max_ms:         float   # round-trip time max   (ms)
    rtt_mdev_ms:        float   # round-trip time mdev  (ms)
    status:             str     # "ok" | "partial_loss" | "timeout" | "error"

    # ── CSV header from field names ──────────────────────────────
    @classmethod
    def csv_header(cls) -> List[str]:
        """Return the CSV column names derived from field names."""
        return [f.name for f in fields(cls)]

    def csv_row(self) -> List:
        """Return values in the same order as csv_header()."""
        return [getattr(self, f.name) for f in fields(self)]


# ──────────────────────────────────────────────────────────────────────
# 2.  PING OUTPUT PARSER
#     Extracts structured data from the raw `ping` stdout.
# ──────────────────────────────────────────────────────────────────────

# Regex for the summary line:  "3 packets transmitted, 3 received, 0% packet loss, …"
_PKT_RE = re.compile(
    r"(\d+)\s+packets?\s+transmitted,\s+(\d+)\s+received"
    r".*?(\d+(?:\.\d+)?)%\s+packet\s+loss",
    re.IGNORECASE,
)

# Regex for the RTT line:  "rtt min/avg/max/mdev = 5.123/5.456/5.789/0.123 ms"
_RTT_RE = re.compile(
    r"rtt\s+min/avg/max/mdev\s*=\s*"
    r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms",
    re.IGNORECASE,
)


def parse_ping(output: str, source: str, destination: str,
               destination_ip: str) -> MonitorRecord:
    """
    Parse raw ``ping`` command output into a :class:`MonitorRecord`.

    Parameters
    ----------
    output : str
        Full stdout from ``ping -c N <ip>``.
    source, destination : str
        Mininet host names (for labelling).
    destination_ip : str
        The IP address that was pinged.

    Returns
    -------
    MonitorRecord
        Populated record.  If the ping completely fails (e.g. host
        unreachable), RTT fields default to ``0.0`` and status is
        ``"timeout"`` or ``"error"``.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    # ── Packet statistics ────────────────────────────────────────
    pkt_match = _PKT_RE.search(output)
    if pkt_match:
        packets_sent     = int(pkt_match.group(1))
        packets_received = int(pkt_match.group(2))
        packet_loss_pct  = float(pkt_match.group(3))
    else:
        # Could not parse — treat as total failure
        packets_sent     = 0
        packets_received = 0
        packet_loss_pct  = 100.0

    # ── RTT statistics ───────────────────────────────────────────
    rtt_match = _RTT_RE.search(output)
    if rtt_match:
        rtt_min  = float(rtt_match.group(1))
        rtt_avg  = float(rtt_match.group(2))
        rtt_max  = float(rtt_match.group(3))
        rtt_mdev = float(rtt_match.group(4))
    else:
        rtt_min = rtt_avg = rtt_max = rtt_mdev = 0.0

    # ── Derive status label ──────────────────────────────────────
    if packet_loss_pct == 0.0 and packets_received > 0:
        status = "ok"
    elif 0.0 < packet_loss_pct < 100.0:
        status = "partial_loss"
    elif packets_received == 0 and packets_sent > 0:
        status = "timeout"
    else:
        status = "error"

    return MonitorRecord(
        timestamp=timestamp,
        source=source,
        destination=destination,
        destination_ip=destination_ip,
        packets_sent=packets_sent,
        packets_received=packets_received,
        packet_loss_pct=packet_loss_pct,
        rtt_min_ms=rtt_min,
        rtt_avg_ms=rtt_avg,
        rtt_max_ms=rtt_max,
        rtt_mdev_ms=rtt_mdev,
        status=status,
    )


# ──────────────────────────────────────────────────────────────────────
# 3.  CSV WRITER
#     Thread-safe, append-only CSV sink.  Creates the file and header
#     row automatically on first write.
# ──────────────────────────────────────────────────────────────────────

class CSVWriter:
    """
    Append-only CSV writer for :class:`MonitorRecord` objects.

    * Creates parent directories + header row on first call.
    * Thread-safe via a simple lock.
    * Flushes after every write so data is never lost on crash.
    """

    def __init__(self, filepath: str | Path):
        self._path = Path(filepath)
        self._lock = threading.Lock()
        self._file = None
        self._writer = None

    # ── lazy open ─────────────────────────────────────────────────
    def _ensure_open(self):
        """Open the CSV file, creating dirs and header if needed."""
        if self._file is not None:
            return

        # Create parent directories (e.g. datasets/)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        file_exists = self._path.exists() and self._path.stat().st_size > 0

        self._file = open(self._path, mode="a", newline="", buffering=1)
        self._writer = csv.writer(self._file)

        # Write header only for a brand-new file
        if not file_exists:
            self._writer.writerow(MonitorRecord.csv_header())
            self._file.flush()

    # ── public API ────────────────────────────────────────────────
    def write(self, record: MonitorRecord):
        """Append a single record to the CSV."""
        with self._lock:
            self._ensure_open()
            self._writer.writerow(record.csv_row())
            self._file.flush()

    def write_many(self, records: List[MonitorRecord]):
        """Append multiple records atomically."""
        with self._lock:
            self._ensure_open()
            for rec in records:
                self._writer.writerow(rec.csv_row())
            self._file.flush()

    def close(self):
        """Flush and close the underlying file."""
        with self._lock:
            if self._file:
                self._file.flush()
                self._file.close()
                self._file = None
                self._writer = None


# ──────────────────────────────────────────────────────────────────────
# 4.  TERMINAL LOGGER
#     Pretty-prints each probe result with colour-coded status.
# ──────────────────────────────────────────────────────────────────────

# ANSI colour codes (gracefully degrade on non-TTY)
_RESET  = "\033[0m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_DIM    = "\033[2m"

_STATUS_COLOUR = {
    "ok":           _GREEN,
    "partial_loss": _YELLOW,
    "timeout":      _RED,
    "error":        _RED,
}


def log_record(record: MonitorRecord, verbose: bool = True):
    """
    Print a human-readable line to the terminal.

    Format:
      [2025-05-09T12:34:56Z]  h1 → h3  |  avg=5.12ms  loss=0.0%  status=ok
    """
    colour = _STATUS_COLOUR.get(record.status, _RESET)
    ts_short = record.timestamp[:19]  # trim sub-seconds for readability

    line = (
        f"{_DIM}[{ts_short}]{_RESET}  "
        f"{_CYAN}{record.source:3s} → {record.destination:3s}{_RESET}  │  "
        f"avg={record.rtt_avg_ms:7.2f}ms  "
        f"loss={record.packet_loss_pct:5.1f}%  "
        f"status={colour}{record.status}{_RESET}"
    )
    print(line)

    if verbose and record.status != "ok":
        # Extra detail on failures for quick debugging
        print(
            f"         ↳ sent={record.packets_sent}  "
            f"recv={record.packets_received}  "
            f"rtt_min={record.rtt_min_ms:.2f}ms  "
            f"rtt_max={record.rtt_max_ms:.2f}ms"
        )


# ──────────────────────────────────────────────────────────────────────
# 5.  SINGLE-ROUND COLLECTION
#     Probes every unique host pair once.  Can be used independently
#     or called repeatedly by NetworkMonitor.
# ──────────────────────────────────────────────────────────────────────

def collect_once(net, ping_count: int = 2,
                 ping_timeout: int = 3) -> List[MonitorRecord]:
    """
    Ping every unique host pair in *net* in parallel and return parsed records.

    Parameters
    ----------
    net : mininet.net.Mininet
        A running Mininet network instance.
    ping_count : int
        Number of ICMP echo requests per probe (``ping -c``).
    ping_timeout : int
        Per-ping timeout in seconds (``ping -W``).

    Returns
    -------
    list[MonitorRecord]
        One record per ordered (src, dst) pair.
    """
    hosts = net.hosts
    records: List[MonitorRecord] = []

    def probe_pair(src, dst) -> MonitorRecord:
        dst_ip = dst.IP()
        try:
            # Execute ping on the Mininet host namespace using isolated Popen for safe concurrency
            cmd = ["ping", "-c", str(ping_count), "-W", str(ping_timeout), dst_ip]
            proc = src.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, _ = proc.communicate()
            output = stdout.decode("utf-8", errors="ignore")
            return parse_ping(output, src.name, dst.name, dst_ip)
        except Exception as exc:
            # Graceful degradation — log the error but keep monitoring
            err_rec = MonitorRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                source=src.name,
                destination=dst.name,
                destination_ip=dst_ip,
                packets_sent=ping_count,
                packets_received=0,
                packet_loss_pct=100.0,
                rtt_min_ms=0.0,
                rtt_avg_ms=0.0,
                rtt_max_ms=0.0,
                rtt_mdev_ms=0.0,
                status="error",
            )
            print(f"  ⚠  Probe {src.name}→{dst.name} raised: {exc}")
            return err_rec

    # Gather unique directed pairs
    pairs = []
    for src in hosts:
        for dst in hosts:
            if src != dst:
                pairs.append((src, dst))

    # Execute all ping probes concurrently using ThreadPoolExecutor
    # Max workers bound by the number of pairs since IO-bound
    with ThreadPoolExecutor(max_workers=max(1, len(pairs))) as executor:
        future_to_pair = {executor.submit(probe_pair, s, d): (s, d) for s, d in pairs}
        for future in as_completed(future_to_pair):
            try:
                res = future.result()
                records.append(res)
            except Exception as exc:
                # Failsafe just in case future.result() itself raises
                pass

    return records


# ──────────────────────────────────────────────────────────────────────
# 6.  CONTINUOUS NETWORK MONITOR
#     Runs collect_once() in a loop on a background thread, writing
#     every record to the CSV and printing live logs.
# ──────────────────────────────────────────────────────────────────────

class NetworkMonitor:
    """
    Background network health monitor for PathGuard.

    Parameters
    ----------
    net : Mininet
        A **running** Mininet network instance.
    csv_path : str | Path
        Where to write / append the CSV dataset.
    interval : float
        Seconds to wait between monitoring rounds.
    ping_count : int
        ICMP packets per probe.
    ping_timeout : int
        Timeout per probe (seconds).
    verbose : bool
        Whether to print extra detail on failures.

    Example
    -------
    >>> mon = NetworkMonitor(net, interval=10)
    >>> mon.start()
    >>> # … run experiments …
    >>> mon.stop()
    """

    DEFAULT_CSV = "datasets/network_data.csv"

    def __init__(
        self,
        net,
        csv_path: str | Path = DEFAULT_CSV,
        interval: float = 10.0,
        ping_count: int = 2,
        ping_timeout: int = 3,
        verbose: bool = True,
        fault_detector=None,
    ):
        self.net = net
        self.csv_path = Path(csv_path)
        self.interval = interval
        self.ping_count = ping_count
        self.ping_timeout = ping_timeout
        self.verbose = verbose
        self.fault_detector = fault_detector  # ai.train_model.FaultDetector

        # Rolling window for metrics (store last 3 rounds per (src, dst) pair)
        self.history = defaultdict(lambda: deque(maxlen=3))
        self.recovery_engine = RecoveryEngine()
        self.is_recovering = False
        self.active_failures = 0
        self.instability_count = 0
        self.latest_health_score = 100

        self._csv = CSVWriter(self.csv_path)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._round = 0

    # ── lifecycle ─────────────────────────────────────────────────

    def start(self):
        """Start the monitoring loop in a daemon thread."""
        if self._thread and self._thread.is_alive():
            print("⚠  Monitor already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="pathguard-monitor", daemon=True
        )
        self._thread.start()
        print(
            f"✓  PathGuard monitor started  "
            f"(interval={self.interval}s, csv={self.csv_path})"
        )

    def stop(self):
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)
        self._csv.close()
        print(f"■  Monitor stopped.  {self._round} rounds recorded.")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── main loop ─────────────────────────────────────────────────

    def _loop(self):
        """Internal loop — runs on the daemon thread."""
        while not self._stop_event.is_set():
            self._round += 1
            self._print_round_header()

            try:
                records = collect_once(
                    self.net,
                    ping_count=self.ping_count,
                    ping_timeout=self.ping_timeout,
                )

                # Write to CSV
                self._csv.write_many(records)

                # Print live terminal logs
                for rec in records:
                    log_record(rec, verbose=self.verbose)

                # AI fault detection (if model is loaded)
                fault_alerts = self._detect_faults(records)

                self._print_round_summary(records, fault_alerts)

            except Exception as exc:
                print(f"  ✖  Round {self._round} failed: {exc}")

            # Wait for the next round (interruptible)
            self._stop_event.wait(timeout=self.interval)

    # ── AI fault detection ────────────────────────────────────────

    def _detect_faults(self, records: List[MonitorRecord]) -> list:
        """Run each record through the FaultDetector if available.

        Returns a list of (record, severity, confidence) tuples for faults/warnings.
        Dynamically adjusts monitoring interval based on severity.
        """
        alerts = []
        highest_severity = "NORMAL"
        
        # Aggregate metrics for health score
        total_rtt = 0.0
        rtt_cnt = 0
        max_loss = 0.0
        active_link_failures = set()

        # Update rolling history and detect early degradation
        for rec in records:
            pair_key = (rec.source, rec.destination)
            self.history[pair_key].append(rec)
            
            if rec.rtt_avg_ms > 0:
                total_rtt += rec.rtt_avg_ms
                rtt_cnt += 1
            
            if rec.packet_loss_pct > max_loss:
                max_loss = rec.packet_loss_pct

            if rec.status in ["timeout", "error"] or rec.packet_loss_pct == 100.0:
                active_link_failures.add(pair_key)
            
            # Moving average calculation
            recent = self.history[pair_key]
            
            # Predict using AI if loaded
            if self.fault_detector:
                try:
                    res = self.fault_detector.predict_advanced(
                        packet_loss_pct=rec.packet_loss_pct,
                        rtt_avg_ms=rec.rtt_avg_ms,
                        rtt_max_ms=rec.rtt_max_ms,
                        rtt_mdev_ms=rec.rtt_mdev_ms,
                    )
                    severity = res["severity"]
                    confidence = res["confidence"]
                    explanation = res["explanation"]
                    
                    # Check if early degradation is detected via moving averages (sliding window)
                    # If current RTT is > 2x the average of prior rounds, flag a spike
                    if len(recent) > 1:
                         prev_rtt_avg = sum(r.rtt_avg_ms for r in list(recent)[:-1]) / (len(recent) - 1)
                         if rec.rtt_avg_ms > max(10.0, prev_rtt_avg * 2.0) and severity == "NORMAL":
                             severity = "WARNING"
                             explanation = "Early degradation detected: RTT spike vs historical"
                             res["severity"] = severity
                             res["explanation"] = explanation

                    if severity == "CRITICAL" or severity == "FAULT":
                        highest_severity = "CRITICAL"
                        alerts.append((rec, severity, confidence))
                        print(
                            f"  {_RED}🚨 CRITICAL DETECTED{_RESET}  "
                            f"{rec.source} → {rec.destination}  "
                            f"confidence={confidence:.0f}%  "
                            f"({explanation})"
                        )
                        # Append to timeline log
                        log_event(f"AI predicted CRITICAL on {rec.source}→{rec.destination}: {explanation} ({confidence:.0f}% conf.)", "CRITICAL")
                    elif severity == "WARNING":
                        if highest_severity != "CRITICAL":
                            highest_severity = "WARNING"
                        alerts.append((rec, severity, confidence))
                        print(
                            f"  {_YELLOW}⚠️ WARNING DETECTED{_RESET}  "
                            f"{rec.source} → {rec.destination}  "
                            f"confidence={confidence:.0f}%"
                            f" ({explanation})"
                        )
                        log_event(f"AI predicted WARNING on {rec.source}→{rec.destination}: {explanation}", "WARNING")
                except Exception as exc:
                    pass
            else:
                # Fallback without AI
                if rec.packet_loss_pct >= 50.0:
                    highest_severity = "CRITICAL"
                elif rec.packet_loss_pct > 0.0:
                    if highest_severity != "CRITICAL":
                        highest_severity = "WARNING"

        # Calculate overall Health Score for this round
        avg_latency = total_rtt / max(1, rtt_cnt)
        self.active_failures = len(active_link_failures)
        
        # Calculate instability: fluctuations in packet loss in history
        instabilities = 0
        for pair_key, recent in self.history.items():
            if len(recent) >= 2:
                losses = [r.packet_loss_pct for r in recent]
                if max(losses) - min(losses) > 20.0: 
                    instabilities += 1
        self.instability_count = instabilities
        
        self.latest_health_score = calculate_health_score(
            avg_latency=avg_latency,
            max_loss=max_loss,
            active_failures=self.active_failures,
            instability_count=self.instability_count
        )
        health_label = get_health_label(self.latest_health_score)
        
        print(f"\n  📊 Network Health: {self.latest_health_score}/100 ({health_label})")

        # Adaptive Monitoring Frequency
        if highest_severity == "CRITICAL":
            self.interval = 1.0
        elif highest_severity == "WARNING":
            self.interval = 3.0
        else:
            self.interval = 10.0

        # Dynamic Recovery Trigger
        if highest_severity == "CRITICAL" and not self.is_recovering:
            self.is_recovering = True
            
            # Smart Fault Diagnosis based on the full triangle topology
            # Topology bottlenecks:
            # - h1/h2 to h4 uses s1-s2
            # - h1/h2 to h3 uses s1-s3
            # - h3 to h4 uses s2-s3
            failed_pairs = list(active_link_failures)
            
            has_s1_s2_fail = any(
                (src in ['h1','h2'] and dst == 'h4') or (src == 'h4' and dst in ['h1','h2'])
                for src, dst in failed_pairs
            )
            has_s1_s3_fail = any(
                (src in ['h1','h2'] and dst == 'h3') or (src == 'h3' and dst in ['h1','h2'])
                for src, dst in failed_pairs
            )
            has_s2_s3_fail = any(
                (src == 'h3' and dst == 'h4') or (src == 'h4' and dst == 'h3')
                for src, dst in failed_pairs
            )
            
            # Set accurate live states
            link_statuses = {"s1-s2": "up", "s2-s3": "up", "s1-s3": "up"}
            suspected_link = "s1-s2" # fallback default
            
            if has_s1_s2_fail:
                suspected_link = "s1-s2"
                link_statuses["s1-s2"] = "down"
            elif has_s1_s3_fail:
                suspected_link = "s1-s3"
                link_statuses["s1-s3"] = "down"
            elif has_s2_s3_fail:
                suspected_link = "s2-s3"
                link_statuses["s2-s3"] = "down"

            # Build accurate metrics representation for current state
            current_metrics = {}
            for lk, state in link_statuses.items():
                if state == "down":
                    current_metrics[lk] = {"loss": 100.0, "latency": 0.0, "status": "down"}
                else:
                    current_metrics[lk] = {"loss": 0.0, "latency": avg_latency if avg_latency > 0 else 5.0, "status": "up"}
                    
            # Execute restoration routine passing global Mininet context
            self.recovery_engine.trigger_recovery(suspected_link, current_metrics, net=self.net)
            self.is_recovering = False

        return alerts

    # ── helpers ───────────────────────────────────────────────────

    def _print_round_header(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n{'─' * 60}")
        print(f"  📡  Round {self._round}  —  {ts}")
        print(f"{'─' * 60}")

    @staticmethod
    def _print_round_summary(records: List[MonitorRecord], fault_alerts=None):
        total   = len(records)
        healthy = sum(1 for r in records if r.status == "ok")
        failed  = total - healthy
        avg_lat = (
            sum(r.rtt_avg_ms for r in records if r.rtt_avg_ms > 0)
            / max(1, sum(1 for r in records if r.rtt_avg_ms > 0))
        )
        colour = _GREEN if failed == 0 else (_YELLOW if failed < total else _RED)
        summary = (
            f"\n  Summary: {colour}{healthy}/{total} healthy{_RESET}  "
            f"avg_latency={avg_lat:.2f}ms  failures={failed}"
        )
        if fault_alerts:
            summary += f"  {_RED}AI faults={len(fault_alerts)}{_RESET}"
        print(summary)


# ──────────────────────────────────────────────────────────────────────
# 7.  STANDALONE ENTRY POINT
#     When run directly, this script builds the PathGuard topology,
#     starts monitoring, and writes data until interrupted (Ctrl-C).
# ──────────────────────────────────────────────────────────────────────

def main():
    """
    Standalone launcher:  build topology → start monitoring → Ctrl-C to stop.

    This mode is useful for unattended data-collection runs.
    For interactive use, import NetworkMonitor inside the topology script
    or the Mininet CLI instead.
    """
    parser = argparse.ArgumentParser(
        description="PathGuard network health monitor"
    )
    parser.add_argument(
        "--csv", default=NetworkMonitor.DEFAULT_CSV,
        help="Output CSV path (default: datasets/network_data.csv)"
    )
    parser.add_argument(
        "--interval", type=float, default=10.0,
        help="Seconds between monitoring rounds (default: 10)"
    )
    parser.add_argument(
        "--ping-count", type=int, default=2,
        help="ICMP packets per probe (default: 2)"
    )
    parser.add_argument(
        "--ping-timeout", type=int, default=3,
        help="Ping timeout in seconds (default: 3)"
    )
    parser.add_argument(
        "--controller-ip", default="127.0.0.1",
        help="SDN controller IP (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--controller-port", type=int, default=6633,
        help="SDN controller port (default: 6633 for POX)"
    )
    args = parser.parse_args()

    # ── Import topology and Mininet components ───────────────────
    # We import here (not at module level) so the monitor module can
    # also be used as a library without requiring Mininet installed.
    from mininet.net import Mininet
    from mininet.node import RemoteController, OVSKernelSwitch
    from mininet.link import TCLink
    from mininet.log import setLogLevel, info

    # Add the project root to the path so we can import the topology
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    from topology.topology import PathGuardTopo, cleanup_mininet

    setLogLevel("info")

    # Clean up stale interfaces from previous Mininet runs
    cleanup_mininet()

    # ── Build and start the network ──────────────────────────────
    info("*** Creating PathGuard topology for monitoring\n")
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
    time.sleep(2)  # let the controller discover the topology

    # Try to load AI model
    fault_detector = None
    model_path = project_root / "ai" / "model.pkl"
    if model_path.exists():
        try:
            from ai.train_model import FaultDetector
            fault_detector = FaultDetector.load(str(model_path))
            info("*** AI model loaded successfully from %s\n" % model_path)
        except Exception as exc:
            info("*** Could not load AI model at startup: %s\n" % exc)

    # ── Start monitoring ─────────────────────────────────────────
    monitor = NetworkMonitor(
        net,
        csv_path=args.csv,
        interval=args.interval,
        ping_count=args.ping_count,
        ping_timeout=args.ping_timeout,
        fault_detector=fault_detector
    )

    # Handle Ctrl-C gracefully
    def _sigint_handler(sig, frame):
        print("\n\n*** Ctrl-C received — shutting down…")
        monitor.stop()
        net.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint_handler)

    print("\n" + "=" * 60)
    print("  PathGuard Monitor — press Ctrl-C to stop")
    print("=" * 60)

    # Run monitor on the main thread (blocking)
    monitor._stop_event.clear()
    monitor._thread = threading.current_thread()

    try:
        monitor._loop()
    except KeyboardInterrupt:
        pass
    finally:
        monitor._csv.close()
        info("*** Stopping network\n")
        net.stop()


if __name__ == "__main__":
    main()
