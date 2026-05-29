#!/usr/bin/env python3
"""
Test Suite: Flask Dashboard Backend
Verifies Flask API endpoints /api/topology and /api/status return
expected JSON structures, schemas, and values.
"""

import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import dashboard.app as dapp

def test_dashboard_index_route():
    """Verify the index page returns 200 and loads HTML template."""
    dapp.app.config['TESTING'] = True
    with dapp.app.test_client() as client:
        res = client.get('/')
        assert res.status_code == 200

def test_api_topology():
    """Verify /api/topology returns nodes and links structure."""
    dapp.app.config['TESTING'] = True
    with dapp.app.test_client() as client:
        res = client.get('/api/topology')
        assert res.status_code == 200
        data = json.loads(res.data.decode())
        
        assert "nodes" in data
        assert "links" in data
        
        # Verify switches and hosts structure
        nodes = data["nodes"]
        links = data["links"]
        
        assert len(nodes) > 0
        assert len(links) > 0
        
        # Verify node fields
        first_node = nodes[0]
        assert "id" in first_node
        assert "label" in first_node
        assert "type" in first_node
        assert "layer" in first_node

def test_api_status_demo_fallback():
    """Verify /api/status falls back gracefully to demo data when no telemetry file exists."""
    dapp.app.config['TESTING'] = True
    
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_csv = Path(tmpdir) / "missing_data.csv"
        # Patch DATA_CSV to a non-existent path
        with patch("dashboard.app.DATA_CSV", fake_csv), \
             patch("dashboard.app.read_runtime_state", return_value=None):
            with dapp.app.test_client() as client:
                res = client.get('/api/status')
                assert res.status_code == 200
                data = json.loads(res.data.decode())
                
                # Check required schema keys present in the demo/fallback data (latest_status.json)
                assert "ai_status" in data
                assert "timestamp" in data
                assert "packet_loss_pct" in data
                assert "rtt_avg_ms" in data
                assert "recovery_status" in data
                assert "links" in data
                assert "chart_data" in data

def test_api_status_with_telemetry():
    """Verify /api/status processes live CSV telemetry and returns enriched API data."""
    dapp.app.config['TESTING'] = True
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_csv = Path(tmpdir) / "network_data.csv"
        tmp_events = Path(tmpdir) / "events.log"
        tmp_metrics = Path(tmpdir) / "recovery_metrics.json"
        
        # Create a mock telemetry CSV row (normal healthy telemetry)
        df = pd.DataFrame([
            {
                "timestamp": "2026-05-24T10:00:00Z",
                "source": "h1",
                "destination": "h2",
                "destination_ip": "10.0.1.1",
                "packets_sent": 3,
                "packets_received": 3,
                "packet_loss_pct": 0.0,
                "rtt_min_ms": 5.0,
                "rtt_avg_ms": 6.2,
                "rtt_max_ms": 8.0,
                "rtt_mdev_ms": 0.4,
                "status": "ok"
            }
        ])
        df.to_csv(tmp_csv, index=False)
        
        # Create a mock events log
        tmp_events.write_text("[10:00:00] INFO: Test event\n")
        
        # Create a mock recovery metrics file
        metrics_data = {
            "recovery_active": False,
            "successful_recoveries": 1,
            "failed_recoveries": 0,
            "average_recovery_time_sec": 0.42,
            "total_recoveries_count": 1,
            "last_recovery": {
                "timestamp": "2026-05-24T10:00:00Z",
                "status": "SUCCESS",
                "failed_link": "s1-s2",
                "selected_path": "Path_1",
                "route": "s8 → s4 → s1",
                "duration_sec": 0.42
            }
        }
        with open(tmp_metrics, "w") as f:
            json.dump(metrics_data, f)
            
        with patch("dashboard.app.DATA_CSV", tmp_csv), \
             patch("dashboard.app.EVENTS_LOG", tmp_events), \
             patch("dashboard.app.MODEL_PKL", Path(tmpdir) / "nonexistent_model.pkl"), \
             patch("dashboard.app.read_runtime_state", return_value=None):
             
             with dapp.app.test_client() as client:
                 res = client.get('/api/status')
                 assert res.status_code == 200
                 data = json.loads(res.data.decode())
                 
                 # Verify metrics computed from CSV
                 assert data["ai_status"] == "NORMAL"
                 assert data["health_score"] >= 85
                 assert data["packet_loss_pct"] == 0.0
                 assert data["rtt_avg_ms"] == 6.2
                 
                 # Verify debug info is populated correctly
                 debug = data["debug_info"]
                 assert "failed_link" in debug
                 assert "active_recovery_path" in debug
                 assert "recovery_trigger_reason" in debug
                 assert "controller_action" in debug
                 
                 # Verify timeline is loaded from log
                 assert len(data["timeline"]) >= 1
                 assert "Test event" in data["timeline"][0]
