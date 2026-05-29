#!/usr/bin/env python3
"""
Test Suite: Network Monitoring
Verifies the parse_ping regex parser, the MonitorRecord dataclass,
and the thread-safe behavior of CSVWriter.
"""

import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.monitor import parse_ping, MonitorRecord, CSVWriter

def test_parse_ping_success():
    """Verify parse_ping parses healthy ping outputs."""
    raw_output = """
PING 10.0.1.1 (10.0.1.1) 56(84) bytes of data.
64 bytes from 10.0.1.1: icmp_seq=1 ttl=64 time=5.12 ms
64 bytes from 10.0.1.1: icmp_seq=2 ttl=64 time=5.45 ms
64 bytes from 10.0.1.1: icmp_seq=3 ttl=64 time=5.78 ms

--- 10.0.1.1 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2003ms
rtt min/avg/max/mdev = 5.123/5.456/5.789/0.123 ms
"""
    record = parse_ping(raw_output, "h1", "h2", "10.0.1.1")
    assert isinstance(record, MonitorRecord)
    assert record.source == "h1"
    assert record.destination == "h2"
    assert record.destination_ip == "10.0.1.1"
    assert record.packets_sent == 3
    assert record.packets_received == 3
    assert record.packet_loss_pct == 0.0
    assert record.rtt_min_ms == 5.123
    assert record.rtt_avg_ms == 5.456
    assert record.rtt_max_ms == 5.789
    assert record.rtt_mdev_ms == 0.123
    assert record.status == "ok"

def test_parse_ping_partial_loss():
    """Verify parse_ping parses degraded ping outputs."""
    raw_output = """
PING 10.0.1.1 (10.0.1.1) 56(84) bytes of data.
64 bytes from 10.0.1.1: icmp_seq=1 ttl=64 time=120 ms
64 bytes from 10.0.1.1: icmp_seq=3 ttl=64 time=130 ms

--- 10.0.1.1 ping statistics ---
3 packets transmitted, 2 received, 33.3% packet loss, time 2003ms
rtt min/avg/max/mdev = 120.0/125.0/130.0/5.0 ms
"""
    record = parse_ping(raw_output, "h1", "h2", "10.0.1.1")
    assert record.packets_sent == 3
    assert record.packets_received == 2
    assert record.packet_loss_pct == 33.3
    assert record.rtt_avg_ms == 125.0
    assert record.status == "partial_loss"

def test_parse_ping_timeout():
    """Verify parse_ping parses complete timeout ping outputs."""
    raw_output = """
PING 10.0.1.1 (10.0.1.1) 56(84) bytes of data.

--- 10.0.1.1 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss, time 2003ms
"""
    record = parse_ping(raw_output, "h1", "h2", "10.0.1.1")
    assert record.packets_sent == 3
    assert record.packets_received == 0
    assert record.packet_loss_pct == 100.0
    assert record.rtt_avg_ms == 0.0
    assert record.status == "timeout"

def test_monitor_record_csv_serialization():
    """Verify MonitorRecord csv_header and csv_row methods match."""
    rec = MonitorRecord(
        timestamp="2026-05-24T10:00:00Z",
        source="h1",
        destination="h2",
        destination_ip="10.0.1.1",
        packets_sent=3,
        packets_received=3,
        packet_loss_pct=0.0,
        rtt_min_ms=1.5,
        rtt_avg_ms=2.0,
        rtt_max_ms=2.5,
        rtt_mdev_ms=0.5,
        status="ok"
    )
    
    header = rec.csv_header()
    row = rec.csv_row()
    
    assert len(header) == len(row)
    assert header[0] == "timestamp"
    assert row[0] == "2026-05-24T10:00:00Z"
    assert header[1] == "source"
    assert row[1] == "h1"
    assert header[-1] == "status"
    assert row[-1] == "ok"

def test_thread_safe_csv_writer():
    """Verify CSVWriter correctly handles concurrent writes from multiple threads."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "test_data.csv"
        writer = CSVWriter(csv_path)
        
        # Define a work function for threads
        records_per_thread = 50
        num_threads = 4
        
        def run_writer(thread_idx):
            for i in range(records_per_thread):
                rec = MonitorRecord(
                    timestamp="2026-05-24T10:00:00Z",
                    source=f"t{thread_idx}",
                    destination="h2",
                    destination_ip="10.0.1.1",
                    packets_sent=3,
                    packets_received=3,
                    packet_loss_pct=0.0,
                    rtt_min_ms=1.0,
                    rtt_avg_ms=1.5,
                    rtt_max_ms=2.0,
                    rtt_mdev_ms=0.1,
                    status="ok"
                )
                writer.write(rec)
                
        threads = []
        for j in range(num_threads):
            t = threading.Thread(target=run_writer, args=(j,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        writer.close()
        
        # Verify the written file content
        assert csv_path.exists()
        lines = csv_path.read_text().splitlines()
        
        # Header + num_threads * records_per_thread lines
        expected_total_lines = 1 + (num_threads * records_per_thread)
        assert len(lines) == expected_total_lines
        
        # Check header
        header_cols = lines[0].split(",")
        assert header_cols == MonitorRecord.csv_header()
        
        # Check row counts per thread source
        thread_counts = {f"t{j}": 0 for j in range(num_threads)}
        for line in lines[1:]:
            cols = line.split(",")
            src = cols[1]
            if src in thread_counts:
                thread_counts[src] += 1
                
        for t_name, count in thread_counts.items():
            assert count == records_per_thread
