#!/usr/bin/env python3
"""
PathGuard: Network Health Scoring System
-----------------------------------------
Computes overall network health scores based on RTT, packet loss, and faults.
"""

from typing import List, Dict

def calculate_health_score(avg_latency: float, max_loss: float, active_failures: int = 0, instability_count: int = 0) -> int:
    """
    Compute Network Health Score (0-100)
    
    Rules:
    - 90-100 = Healthy
    - 60-89 = Degraded
    - below 60 = Critical
    """
    score = 100
    
    # Penalize loss heavily (e.g., max loss subtracts up to 100 points)
    # Deduction of 2 points per 1% loss
    loss_penalty = max_loss * 2.0
    score -= min(100.0, loss_penalty)
    
    # Penalize latency
    # E.g., subtract 1 point per 5ms average latency
    latency_penalty = avg_latency / 5.0
    score -= min(30.0, latency_penalty)
    
    # Penalize explicit active failures (e.g., nodes down)
    score -= (active_failures * 20)
    
    # Penalize instabilities
    score -= (instability_count * 10)
    
    # Clamp to [0, 100]
    final_score = max(0, min(100, int(score)))
    return final_score

def get_health_label(score: int) -> str:
    if score >= 90:
        return "Healthy"
    elif score >= 60:
        return "Degraded"
    else:
        return "Critical"
