#!/usr/bin/env python3
"""
PathGuard Dashboard Backend
Serves the web interface and provides real-time API endpoints for network status.
"""

import os
import sys
import time
import json
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify
import pandas as pd

# Add project root to path so we can import AI module
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

try:
    from ai.train_model import FaultDetector
except ImportError:
    FaultDetector = None

try:
    from recovery.path_selector import PathRanker
    path_ranker = PathRanker()
except ImportError:
    path_ranker = None

try:
    from monitoring.health import calculate_health_score
except ImportError:
    def calculate_health_score(avg_latency, max_loss, **kwargs):
        return max(0, min(100, int(100 - (max_loss * 2.0) - (avg_latency / 5.0))))

app = Flask(__name__)

# Constants
DATA_CSV = project_root / "datasets" / "network_data.csv"
MODEL_PKL = project_root / "ai" / "model.pkl"
DEMO_DATA_FILE = Path(__file__).parent / "data" / "latest_status.json"
EVENTS_LOG = project_root / "results" / "events.log"

# Load the AI model once at startup if available
ai_detector = None
if MODEL_PKL.exists() and FaultDetector:
    try:
        ai_detector = FaultDetector.load(MODEL_PKL)
        print(f"Loaded AI model from {MODEL_PKL}")
    except Exception as e:
        print(f"Failed to load AI model: {e}")

# In-memory history for charts (last 20 points)
history = {
    "labels": [],
    "latency": [],
    "loss": []
}

def get_demo_data():
    """Fallback if live data is unavailable."""
    if DEMO_DATA_FILE.exists():
        with open(DEMO_DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ai_status": "NORMAL",
        "confidence": 100.0,
        "explanation": "Demo Mode",
        "health_score": 100,
        "packet_loss_pct": 0.0,
        "rtt_avg_ms": 0.0,
        "recovery_status": "Demo Mode (No Live Data)",
        "links": {"s1-s2": "up", "s2-s3": "up", "s1-s3": "up"},
        "chart_data": history,
        "path_rankings": [],
        "timeline": []
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def status():
    """
    Reads the latest network data and runs AI inference.
    """
    if not DATA_CSV.exists() or DATA_CSV.stat().st_size == 0:
        return jsonify(get_demo_data())

    try:
        # Read the recent portion of the CSV (to avoid loading a massive file)
        # We read the last 50 rows which should cover the most recent monitoring round (12 pairs)
        df = pd.read_csv(DATA_CSV)
        if df.empty:
            return jsonify(get_demo_data())
            
        # Get the latest timestamp
        latest_ts = df["timestamp"].max()
        latest_df = df[df["timestamp"] == latest_ts]

        if latest_df.empty:
             return jsonify(get_demo_data())

        # Aggregate metrics for the dashboard
        avg_latency = latest_df[latest_df["rtt_avg_ms"] > 0]["rtt_avg_ms"].mean()
        if pd.isna(avg_latency):
            avg_latency = 0.0
            
        max_loss = latest_df["packet_loss_pct"].max()
        
        # AI Detection & Advanced Inference
        overall_status = "NORMAL"
        confidence = 100.0
        explanation = "Traffic is stable"
        links_status = {"s1-s2": "up", "s2-s3": "up", "s1-s3": "up"}
        
        if ai_detector:
            # Predict each row in the latest round
            predictions = ai_detector.predict_batch_advanced(latest_df)
            severities = [p['severity'] for p in predictions]
            
            if "CRITICAL" in severities:
                overall_status = "CRITICAL"
            elif "WARNING" in severities:
                overall_status = "WARNING"
            else:
                overall_status = "NORMAL"

            # Find the prediction that caused the worst severity
            worst_pred = next((p for p in predictions if p['severity'] == overall_status), predictions[0])
            confidence = worst_pred['confidence']
            explanation = worst_pred['explanation']
            
            if max_loss >= 50:
                links_status["s1-s2"] = "down"
            elif max_loss > 0 or overall_status == "WARNING":
                links_status["s1-s2"] = "warning"
                
        else:
            # Fallback
            if max_loss >= 50:
                overall_status = "CRITICAL"
                links_status["s1-s2"] = "down"
            elif max_loss >= 10:
                overall_status = "WARNING"
                links_status["s1-s2"] = "warning"

        # Compute Network Health Score (0-100) via centralized rules
        active_failures = len(latest_df[latest_df["status"].isin(["timeout", "error"])])
        health_score = calculate_health_score(
            avg_latency=avg_latency,
            max_loss=max_loss,
            active_failures=active_failures
        )

        # Smart Path Ranking
        path_rankings = []
        if path_ranker:
            metrics_dict = {
                "s1-s2": {"loss": max_loss, "latency": avg_latency, "status": links_status["s1-s2"]}
            }
            ranks = path_ranker.evaluate_paths(metrics_dict)
            path_rankings = [{"path": r.path_name, "route": " \u2192 ".join(r.switches), "score": r.score} for r in ranks]

        # Read Event Timeline
        timeline = []
        if EVENTS_LOG.exists():
            with open(EVENTS_LOG, "r") as f:
                lines = f.readlines()
                # Get the last 10 events
                timeline = [line.strip() for line in lines[-10:] if line.strip()]

        # Format timestamp for display (just HH:MM:SS)
        try:
            time_obj = datetime.strptime(latest_ts[:19], "%Y-%m-%dT%H:%M:%S")
            time_str = time_obj.strftime("%H:%M:%S")
        except:
            time_str = datetime.utcnow().strftime("%H:%M:%S")

        # Update history
        if len(history["labels"]) == 0 or history["labels"][-1] != time_str:
            history["labels"].append(time_str)
            history["latency"].append(round(avg_latency, 2))
            history["loss"].append(round(max_loss, 2))

            # Keep only last 20 points
            if len(history["labels"]) > 20:
                history["labels"] = history["labels"][-20:]
                history["latency"] = history["latency"][-20:]
                history["loss"] = history["loss"][-20:]

        # Read recovery metrics
        recovery_status = "Idle"
        RECOVERY_METRICS = project_root / "results" / "recovery_metrics.json"
        if RECOVERY_METRICS.exists():
            try:
                with open(RECOVERY_METRICS, "r") as f:
                    rec_data = json.load(f)
                last_rec = rec_data.get("last_recovery", {})
                if last_rec:
                    rec_path = last_rec.get("selected_path", "None")
                    rec_dur = last_rec.get("duration_sec", 0.0)
                    recovery_status = f"Recovered ({rec_path})"
            except:
                pass
        
        if overall_status == "CRITICAL" and "Recovered" not in recovery_status:
            recovery_status = "Triggering Recovery..."

        data = {
            "timestamp": latest_ts,
            "ai_status": overall_status,
            "confidence": round(confidence, 1),
            "explanation": explanation,
            "health_score": health_score,
            "packet_loss_pct": round(max_loss, 2),
            "rtt_avg_ms": round(avg_latency, 2),
            "recovery_status": recovery_status,
            "links": links_status,
            "chart_data": history,
            "path_rankings": path_rankings,
            "timeline": timeline
        }
        return jsonify(data)
    except Exception as e:
        print(f"Error serving status: {e}")
        return jsonify(get_demo_data())

if __name__ == "__main__":
    print("Starting PathGuard Dashboard on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
