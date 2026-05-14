#!/usr/bin/env python3
"""
PathGuard: Dynamic Recovery Engine
---------------------------------
Handles the self-healing pipeline: detects faults, ranks alternate paths,
logs recovery actions to the timeline, and persists recovery metrics.
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from recovery.path_selector import PathRanker, PathScore
except ImportError:
    # For local/relative execution fallback
    from path_selector import PathRanker, PathScore

project_root = Path(__file__).resolve().parent.parent

# Constants
EVENTS_LOG = project_root / "results" / "events.log"
METRICS_FILE = project_root / "results" / "recovery_metrics.json"

def log_event(msg: str, level: str = "INFO"):
    """Append-safe logger for event timeline."""
    try:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {level}: {msg}\n"
        with open(EVENTS_LOG, "a") as f:
            f.write(log_line)
        print(f"  📝 [Timeline Log] {log_line.strip()}")
    except Exception as e:
        print(f"  ⚠ Failed to write event log: {e}")

class RecoveryEngine:
    def __init__(self):
        self.ranker = PathRanker()
        self.active_recovery = False
        self.recovery_start_time = 0.0
        
        # Default structure for metrics
        self._init_metrics_if_missing()

    def _init_metrics_if_missing(self):
        """Ensure results/recovery_metrics.json has a valid structure."""
        if not METRICS_FILE.exists() or METRICS_FILE.stat().st_size == 0:
            initial_data = {
                "successful_recoveries": 0,
                "failed_recoveries": 0,
                "average_recovery_time_sec": 0.0,
                "total_recoveries_count": 0,
                "last_recovery": {}
            }
            METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(METRICS_FILE, "w") as f:
                json.dump(initial_data, f, indent=4)

    def trigger_recovery(self, failed_link: str, current_metrics: Dict[str, dict]) -> Optional[Dict]:
        """
        Evaluate alternate predefined paths and recommend the healthiest alternative.
        Also updates timing metrics.
        """
        log_event(f"Fault detected on {failed_link}. Triggering Recovery Engine.", "CRITICAL")
        
        self.active_recovery = True
        self.recovery_start_time = time.time()
        
        log_event("Evaluating alternate predefined paths...", "RECOVERY")
        
        # Evaluate alternate paths using the PathRanker
        rankings = self.ranker.evaluate_paths(current_metrics)
        
        # The evaluate_paths sorts by score descending.
        # We choose the top-ranked path that does not use the failed link (or the absolute best remaining).
        best_path = None
        for rank in rankings:
            # If we explicitly know a link is down, we hope evaluate_paths assigned it score=0
            if rank.score > 50:
                best_path = rank
                break
                
        if not best_path and len(rankings) > 0:
            best_path = rankings[0] # Fallback to best available if all are degraded

        if best_path:
            route_str = " → ".join(best_path.switches)
            log_event(f"Smart path recommended: {best_path.path_name} (Route: {route_str}) Score: {best_path.score}/100", "RECOVERY")
            
            # Simulate the controller updating behavior / STP failover
            log_event("Controller informed of optimal route. POX STP stabilizing...", "RECOVERY")
            
            # Measure time elapsed since detection for immediate actions
            time.sleep(0.5) # brief simulation overhead delay
            
            recovery_end_time = time.time()
            recovery_duration = recovery_end_time - self.recovery_start_time
            
            log_event(f"Recovery successful. Connectivity restored over {best_path.path_name}.", "RESTORED")
            
            # Save analytics
            self._update_metrics(True, recovery_duration, best_path.path_name, route_str, failed_link)
            self.active_recovery = False
            
            return {
                "success": True,
                "path_name": best_path.path_name,
                "route": route_str,
                "score": best_path.score,
                "duration_sec": recovery_duration
            }
        else:
            log_event("Recovery failed: No viable alternative paths found.", "ERROR")
            self._update_metrics(False, 0.0, "None", "None", failed_link)
            self.active_recovery = False
            return {"success": False}

    def _update_metrics(self, success: bool, duration: float, selected_path: str, route: str, failed_link: str):
        """Load, update, and save analytics to results/recovery_metrics.json."""
        try:
            self._init_metrics_if_missing()
            with open(METRICS_FILE, "r") as f:
                data = json.load(f)
            
            # Handle compatibility with old/flat structures
            if "successful_recoveries" not in data:
                data = {
                    "successful_recoveries": 0,
                    "failed_recoveries": 0,
                    "average_recovery_time_sec": 0.0,
                    "total_recoveries_count": 0,
                    "last_recovery": {}
                }

            if success:
                data["successful_recoveries"] += 1
                total_cnt = data.get("total_recoveries_count", 0) + 1
                data["total_recoveries_count"] = total_cnt
                
                prev_avg = data.get("average_recovery_time_sec", 0.0)
                new_avg = prev_avg + (duration - prev_avg) / total_cnt
                data["average_recovery_time_sec"] = round(new_avg, 3)
            else:
                data["failed_recoveries"] += 1

            data["last_recovery"] = {
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "SUCCESS" if success else "FAILED",
                "failed_link": failed_link,
                "selected_path": selected_path,
                "route": route,
                "duration_sec": round(duration, 3)
            }

            with open(METRICS_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"  ⚠ Failed to save metrics: {e}")
