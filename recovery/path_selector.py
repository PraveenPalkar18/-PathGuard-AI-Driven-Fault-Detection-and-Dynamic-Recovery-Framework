#!/usr/bin/env python3
"""
PathGuard: Smart Path Ranking System
------------------------------------
Scores and ranks available network paths based on live metrics.
Uses dynamic graph computation from TopoGraph.
"""

from dataclasses import dataclass
from typing import List, Dict
import os
import sys

# Ensure project root is in path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from topology.topo_graph import TopoGraph

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
        self.topo = TopoGraph()

    def evaluate_paths(self, src_switch: str, dst_switch: str, latest_metrics: Dict[str, dict], excluded_links: List[str] = None) -> List[PathScore]:
        """
        Evaluate and rank paths between src_switch and dst_switch based on current link metrics.
        latest_metrics format: { "s1-s2": {"loss": 0, "latency": 5.0, "status": "up", "mdev": 0.2}, ... }
        """
        results = []
        if excluded_links is None:
            excluded_links = []
        
        # Get up to 5 shortest paths, strictly excluding any failed/down links
        candidate_paths = self.topo.k_shortest_paths(src_switch, dst_switch, k=5, excluded_links=excluded_links)
        
        for idx, nodes in enumerate(candidate_paths):
            p_name = f"Path_{idx+1}"
            path_loss = 0.0
            path_latency = 0.0
            path_mdev = 0.0
            path_status = "up"
            has_warning_links = False
            
            # Reconstruct links used by this path
            for i in range(len(nodes) - 1):
                u, v = nodes[i], nodes[i+1]
                link_name = f"{min(u, v)}-{max(u, v)}"
                alt_name = f"{u}-{v}"
                rev_name = f"{v}-{u}"
                link_data = latest_metrics.get(link_name) or latest_metrics.get(alt_name) or latest_metrics.get(rev_name)
                
                is_excluded = (link_name in excluded_links or 
                               alt_name in excluded_links or 
                               rev_name in excluded_links)
                               
                if link_data:
                    if link_data.get("loss", 0.0) > path_loss:
                        path_loss = link_data["loss"]
                    path_latency += link_data.get("latency", 0.0)
                    path_mdev += link_data.get("mdev", 0.0)
                    if link_data.get("status") == "down" or is_excluded:
                        path_status = "down"
                    elif link_data.get("status") == "warning":
                        has_warning_links = True
                else:
                    # Baseline for unknown links
                    path_latency += 5.0
            
            # Compute score: 100 is perfect.
            score = 100.0
            
            if path_status == "down" or path_loss >= 50.0:
                score = 0.0
            else:
                # 1. Packet Loss Penalty (highly severe)
                score -= (path_loss * 2.0)
                
                # 2. Latency Penalty (moderate scaling to avoid dropping to 0)
                if path_latency > 10.0:
                    score -= min(30.0, (path_latency - 10.0) * 0.3)
                
                # 3. Hop Penalty (encourage shorter paths, but not at the cost of failing)
                score -= min(15.0, (len(nodes) - 1) * 2.0)
                
                # 4. Jitter / Instability Penalty
                score -= min(15.0, path_mdev * 2.0)
                
                # 5. Warning Links Penalty
                if has_warning_links:
                    score -= 10.0
                
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
        if not rankings:
            print("    No available paths.")
            return
            
        for rank in rankings:
            color = "\033[92m" if rank.score > 80 else ("\033[93m" if rank.score > 40 else "\033[91m")
            reset = "\033[0m"
            print(f"    {color}{rank.path_name:10s} Score: {rank.score:3d}/100{reset}  "
                  f"(Loss: {rank.loss}%, Latency: {rank.latency:.1f}ms) "
                  f"Route: {' -> '.join(rank.switches)}")

