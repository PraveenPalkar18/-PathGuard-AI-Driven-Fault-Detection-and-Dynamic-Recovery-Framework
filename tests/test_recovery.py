#!/usr/bin/env python3
"""
Test Suite: Recovery and Self-Healing Engine
Verifies the PathRanker score calculation and sorting,
the RecoveryEngine REST triggering payload, and metrics collection.
"""

import sys
import json
import tempfile
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recovery.path_selector import PathRanker
from recovery.recover import RecoveryEngine

def test_path_ranker_normal():
    """Verify PathRanker ranks healthy shortest paths highest."""
    ranker = PathRanker()
    
    # Healthy metrics
    metrics = {
        "s4-s8": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s8-s5": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s4-s1": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s5-s1": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s1-s2": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s2-s6": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s6-s12": {"loss": 0.0, "latency": 2.0, "status": "up"},
    }
    
    rankings = ranker.evaluate_paths("s8", "s12", metrics)
    assert len(rankings) > 0
    # The best score should be very high
    assert rankings[0].score >= 80
    assert rankings[0].status == "up"

def test_path_ranker_failures():
    """Verify PathRanker discounts paths containing down links."""
    ranker = PathRanker()
    
    # Let's say s8-s4 is down
    metrics = {
        "s8-s4": {"loss": 100.0, "latency": 0.0, "status": "down"},
        "s8-s5": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s5-s1": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s1-s2": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s2-s6": {"loss": 0.0, "latency": 2.0, "status": "up"},
        "s6-s12": {"loss": 0.0, "latency": 2.0, "status": "up"},
    }
    
    rankings = ranker.evaluate_paths("s8", "s12", metrics)
    
    # Path using s8-s4 should have score 0 and status "down"
    failed_paths = [r for r in rankings if "s4" in r.switches]
    for p in failed_paths:
        assert p.score == 0
        assert p.status == "down"

def test_recovery_engine_trigger():
    """Verify RecoveryEngine constructs correct JSON REST payload and updates metrics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_metrics_file = Path(tmpdir) / "recovery_metrics.json"
        tmp_events_file = Path(tmpdir) / "events.log"
        
        # Patch the file locations inside recovery.recover
        with patch("recovery.recover.METRICS_FILE", tmp_metrics_file), \
             patch("recovery.recover.EVENTS_LOG", tmp_events_file):
             
             engine = RecoveryEngine()
             
             # Mock the urllib.request.urlopen call
             mock_response = MagicMock()
             mock_response.read.return_value = b'{"status": "success", "applied_links": ["s1-s2"]}'
             mock_response.__enter__.return_value = mock_response
             
             metrics = {
                 "s1-s2": {"loss": 100.0, "latency": 0.0, "status": "down"}
             }
             
             with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
                 res = engine.trigger_recovery(
                     failed_links=["s1-s2"],
                     current_metrics=metrics,
                     net=None
                 )
                 
                 # Verify REST request was made
                 assert mock_urlopen.called
                 req_arg = mock_urlopen.call_args[0][0]
                 assert isinstance(req_arg, urllib.request.Request)
                 assert req_arg.full_url == "http://127.0.0.1:8080/reroute"
                 assert req_arg.headers["Content-type"] == "application/json"
                 
                 # Verify successful recovery response returned
                 assert res is not None
                 assert res["success"] is True
                 assert res["path_name"].startswith("Path_")
                 
                 # Verify metrics file was created and is valid
                 assert tmp_metrics_file.exists()
                 with open(tmp_metrics_file, "r") as f:
                     m_data = json.load(f)
                     assert m_data["successful_recoveries"] == 1
                     assert m_data["total_recoveries_count"] == 1
                     assert m_data["last_recovery"]["status"] == "SUCCESS"
                     assert m_data["last_recovery"]["failed_link"] == "s1-s2"
                     
                 # Verify events log file has relevant entries
                 assert tmp_events_file.exists()
                 events_content = tmp_events_file.read_text()
                 assert "Triggering dynamic SDN reroute" in events_content
                 assert "Successfully notified POX" in events_content
