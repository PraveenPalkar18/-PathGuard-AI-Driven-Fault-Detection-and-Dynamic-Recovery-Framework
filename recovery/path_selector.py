#!/usr/bin/env python3
"""
PathGuard: Smart Path Ranking System
------------------------------------
Scores and ranks available network paths based on live metrics.
"""

from dataclasses import dataclass
from typing import List, Dict

@dataclass
class PathScore:
    path_name: str
    switches: List[str]
    score: int  # 0-100 health score
    latency: float
    loss: float
    status: str

class PathRanker:
    def __init__(self):
        # Define some basic paths between h1/h2 and h3/h4 in the topology
        self.paths = {
            "Path_A": ["s1", "s2", "s3"],       # Typical primary route
            "Path_B": ["s1", "s3"],             # Direct s1-s3 route
            "Path_C": ["s1", "s2"],             # Only to s2
        }

    def evaluate_paths(self, latest_metrics: Dict[str, dict]) -> List[PathScore]:
        """
        Evaluate and rank paths based on current link metrics.
        latest_metrics format: { "s1-s2": {"loss": 0, "latency": 5.0, "status": "up"}, ... }
        """
        results = []
        for p_name, nodes in self.paths.items():
            path_loss = 0.0
            path_latency = 0.0
            path_status = "up"
            
            # Reconstruct links used by this path
            for i in range(len(nodes) - 1):
                # Try both directions
                link1 = f"{nodes[i]}-{nodes[i+1]}"
                link2 = f"{nodes[i+1]}-{nodes[i]}"
                
                link_data = latest_metrics.get(link1) or latest_metrics.get(link2)
                if link_data:
                    # We take the worst loss on the path, and sum the latency
                    if link_data["loss"] > path_loss:
                        path_loss = link_data["loss"]
                    path_latency += link_data["latency"]
                    if link_data["status"] == "down":
                        path_status = "down"
                else:
                    # If we don't have data, assume it's okay for now, or just add baseline
                    path_latency += 5.0
            
            # Compute score: 100 is perfect.
            # Loss heavily penalizes the score. Latency penalizes slightly.
            score = 100
            
            if path_status == "down" or path_loss == 100:
                score = 0
            else:
                score -= path_loss * 2  # 10% loss = -20 points
                score -= (path_latency / 10) # 50ms latency = -5 points
                
            score = max(0, min(100, int(score)))
            
            results.append(PathScore(
                path_name=p_name,
                switches=nodes,
                score=score,
                latency=path_latency,
                loss=path_loss,
                status=path_status
            ))
            
        # Sort by score descending (best path first)
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def print_rankings(self, rankings: List[PathScore]):
        print("\n  🔍 Smart Path Rankings:")
        for rank in rankings:
            color = "\033[92m" if rank.score > 80 else ("\033[93m" if rank.score > 40 else "\033[91m")
            reset = "\033[0m"
            print(f"    {color}{rank.path_name:10s} Score: {rank.score:3d}/100{reset}  "
                  f"(Loss: {rank.loss}%, Latency: {rank.latency:.1f}ms) "
                  f"Route: {' -> '.join(rank.switches)}")
