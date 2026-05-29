#!/usr/bin/env python3
"""
Test Suite: AI Detection Engine
Verifies that FaultDetector loads successfully, classifies network severity state
consistently under normal, warning, and critical metrics, and generates explainable metrics.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.train_model import FaultDetector
from topology.topo_graph import TopoGraph

def test_load_detector():
    """Verify the Random Forest model loads successfully."""
    model_path = ROOT / "ai" / "model.pkl"
    assert model_path.exists()
    
    detector = FaultDetector.load(model_path)
    assert detector is not None
    assert hasattr(detector, "predict_advanced")

def test_classify_normal():
    """Verify that low RTT and 0% loss are classified as NORMAL."""
    model_path = ROOT / "ai" / "model.pkl"
    detector = FaultDetector.load(model_path)
    topo = TopoGraph()
    
    # 0% loss, low RTT
    res = detector.predict_advanced(
        packet_loss_pct=0.0,
        rtt_avg_ms=8.0,
        rtt_max_ms=10.0,
        rtt_mdev_ms=0.5,
        source="h1",
        destination="h6",
        topo=topo
    )
    
    assert res["severity"] == "NORMAL"
    assert "stable" in res["explanation"].lower()
    assert res["confidence"] >= 50.0  # Confidence should be reasonable

def test_classify_warning_loss():
    """Verify mild packet loss triggers WARNING."""
    model_path = ROOT / "ai" / "model.pkl"
    detector = FaultDetector.load(model_path)
    topo = TopoGraph()
    
    # Mild loss
    res = detector.predict_advanced(
        packet_loss_pct=10.0,
        rtt_avg_ms=35.0,
        rtt_max_ms=45.0,
        rtt_mdev_ms=6.0,
        source="h1",
        destination="h6",
        topo=topo
    )
    
    assert res["severity"] == "WARNING"
    assert "packet loss" in res["explanation"].lower()
    assert res["affected_link"] == "s4-s8"  # Should identify link between h1's switch s8 and s4

def test_classify_warning_latency():
    """Verify high RTT triggers WARNING."""
    model_path = ROOT / "ai" / "model.pkl"
    detector = FaultDetector.load(model_path)
    topo = TopoGraph()
    
    # Elevated RTT
    res = detector.predict_advanced(
        packet_loss_pct=0.0,
        rtt_avg_ms=50.0,
        rtt_max_ms=70.0,
        rtt_mdev_ms=8.0,
        source="h1",
        destination="h6",
        topo=topo
    )
    
    assert res["severity"] == "WARNING"
    assert "rtt" in res["explanation"].lower()

def test_classify_critical_loss():
    """Verify high packet loss triggers CRITICAL."""
    model_path = ROOT / "ai" / "model.pkl"
    detector = FaultDetector.load(model_path)
    topo = TopoGraph()
    
    # High packet loss
    res = detector.predict_advanced(
        packet_loss_pct=85.0,
        rtt_avg_ms=10.0,
        rtt_max_ms=20.0,
        rtt_mdev_ms=1.5,
        source="h1",
        destination="h6",
        topo=topo
    )
    
    assert res["severity"] == "CRITICAL"
    assert "severe" in res["explanation"].lower()

def test_classify_critical_latency():
    """Verify severe RTT spikes trigger CRITICAL."""
    model_path = ROOT / "ai" / "model.pkl"
    detector = FaultDetector.load(model_path)
    topo = TopoGraph()
    
    # Severe RTT spike
    res = detector.predict_advanced(
        packet_loss_pct=0.0,
        rtt_avg_ms=95.0,
        rtt_max_ms=140.0,
        rtt_mdev_ms=20.0,
        source="h1",
        destination="h6",
        topo=topo
    )
    
    assert res["severity"] == "CRITICAL"
    assert "critical rtt spike" in res["explanation"].lower()
