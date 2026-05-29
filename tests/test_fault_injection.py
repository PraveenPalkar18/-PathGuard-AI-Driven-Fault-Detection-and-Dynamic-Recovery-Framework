#!/usr/bin/env python3
"""
Test Suite: Fault Injection Scenarios
Simulates 5 distinct network fault telemetry patterns:
  1. Single backbone failure
  2. Distribution congestion
  3. Access-layer degradation
  4. Packet-loss injection
  5. Cascading failures
Verifies they trigger correct WARNING/CRITICAL state transitions
and consistent metrics in analyze_network_state.
"""

import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.fault_analyzer import analyze_network_state
from topology.topo_graph import TopoGraph

def test_scenario_1_backbone_failure():
    """Test 1: Single backbone core link failure (s1-s2 link failure)."""
    topo = TopoGraph()
    # Mock telemetry reporting h1 to h6 (which traverses s8->s4->s9->h6)
    # reporting timeout / complete packet loss.
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00Z",
            "source": "h1", "destination": "h6",
            "destination_ip": "10.0.2.1",
            "packets_sent": 3, "packets_received": 0,
            "packet_loss_pct": 100.0,
            "rtt_avg_ms": 0.0, "rtt_max_ms": 0.0, "rtt_mdev_ms": 0.0,
            "status": "timeout"
        }
    ])
    
    result = analyze_network_state(df, topo)
    assert result["ai_status"] == "CRITICAL"
    assert result["health_score"] < 60
    
    # Check that failed link is identified
    failed_links = [f["link"] for f in result["fault_analysis"]["failed_links"]]
    assert len(failed_links) > 0
    # The default shortest path from h1 to h6 will traverse s8->s4->s9,
    # so the fault should pin it on one of these links (normalized: s4-s8 or s4-s9).
    assert any(lk in failed_links for lk in ["s4-s8", "s4-s9"])

def test_scenario_2_distribution_congestion():
    """Test 2: Distribution layer congestion (s4-s1 high latency)."""
    topo = TopoGraph()
    # High RTT but 0% loss
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00Z",
            "source": "h1", "destination": "h6",
            "destination_ip": "10.0.2.1",
            "packets_sent": 3, "packets_received": 3,
            "packet_loss_pct": 0.0,
            "rtt_avg_ms": 45.0, "rtt_max_ms": 50.0, "rtt_mdev_ms": 1.2,
            "status": "ok"
        }
    ])
    
    result = analyze_network_state(df, topo)
    assert result["ai_status"] == "WARNING"
    assert result["health_score"] < 95
    
    # Check that the links are flagged as warnings or degraded
    degraded = [d["link"] for d in result["fault_analysis"]["degraded_links"]]
    assert len(degraded) > 0

def test_scenario_3_access_layer_degradation():
    """Test 3: Access-layer link degradation (s8-s4 instability / jitter)."""
    topo = TopoGraph()
    # High standard deviation (mdev) indicating high jitter/instability
    # Spans h1 to h6 to cross switch boundaries so the link state engine analyzes it.
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00Z",
            "source": "h1", "destination": "h6",
            "destination_ip": "10.0.2.1",
            "packets_sent": 3, "packets_received": 3,
            "packet_loss_pct": 0.0,
            "rtt_avg_ms": 20.0, "rtt_max_ms": 40.0, "rtt_mdev_ms": 8.0,  # 8/20 = 40% ratio (exceeds 25% threshold)
            "status": "ok"
        }
    ])
    
    result = analyze_network_state(df, topo)
    assert result["ai_status"] == "WARNING"
    assert "instability" in result["explanation"].lower() or "degraded" in result["explanation"].lower()

def test_scenario_4_packet_loss_injection():
    """Test 4: Moderate packet loss injection (e.g. 1.5% loss)."""
    topo = TopoGraph()
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00Z",
            "source": "h1", "destination": "h6",
            "destination_ip": "10.0.2.1",
            "packets_sent": 1000, "packets_received": 985,
            "packet_loss_pct": 1.5,
            "rtt_avg_ms": 8.0, "rtt_max_ms": 12.0, "rtt_mdev_ms": 0.5,
            "status": "partial_loss"
        }
    ])
    
    result = analyze_network_state(df, topo)
    assert result["ai_status"] == "WARNING"
    assert 60 <= result["health_score"] < 85  # Should be WARNING (Degraded) rather than CRITICAL
    assert result["packet_loss_pct"] == 1.5

def test_scenario_5_cascading_failures():
    """Test 5: Cascading failures (multiple links down sequentially/simultaneously)."""
    topo = TopoGraph()
    # Multiple links experiencing failures
    df = pd.DataFrame([
        {
            "timestamp": "2026-05-24T10:00:00Z",
            "source": "h1", "destination": "h6",
            "destination_ip": "10.0.2.1",
            "packets_sent": 3, "packets_received": 0,
            "packet_loss_pct": 100.0,
            "rtt_avg_ms": 0.0, "rtt_max_ms": 0.0, "rtt_mdev_ms": 0.0,
            "status": "timeout"
        },
        {
            "timestamp": "2026-05-24T10:00:00Z",
            "source": "h11", "destination": "h21",
            "destination_ip": "10.0.5.1",
            "packets_sent": 3, "packets_received": 0,
            "packet_loss_pct": 100.0,
            "rtt_avg_ms": 0.0, "rtt_max_ms": 0.0, "rtt_mdev_ms": 0.0,
            "status": "error"
        }
    ])
    
    result = analyze_network_state(df, topo)
    assert result["ai_status"] == "CRITICAL"
    assert result["health_score"] < 40  # Extremely degraded
    assert len(result["fault_analysis"]["failed_links"]) >= 2
