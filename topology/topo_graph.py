#!/usr/bin/env python3
"""
PathGuard: Topology Graph Utilities
-----------------------------------
A pure-Python graph representation of the SDN topology.
Provides shortest-path routing, k-shortest paths, and port mapping lookups
without depending on Mininet objects. This acts as the single source of truth
for both the POX controller and the recovery engine.
"""

import json
import os
from collections import deque
from typing import Dict, List, Tuple, Set, Optional

class TopoGraph:
    def __init__(self, port_map_path: str = None):
        if port_map_path is None:
            # Default to the same directory as this file
            port_map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "port_map.json")
        
        with open(port_map_path, "r") as f:
            self.data = json.load(f)
            
        self.switches = self.data.get("switches", {})
        self.hosts = self.data.get("hosts", {})
        self.links = self.data.get("links", [])
        
        # Build adjacency list for switch-to-switch routing
        self.adj: Dict[str, List[str]] = {sw: [] for sw in self.switches}
        for src, targets in self.switches.items():
            for dst in targets:
                if dst.startswith('s'): # Only add switch-switch links to graph
                    self.adj[src].append(dst)

    def get_port(self, src: str, dst: str) -> Optional[int]:
        """Get the output port on src switch towards dst node."""
        return self.switches.get(src, {}).get(dst)

    def get_host_mac(self, host: str) -> Optional[str]:
        return self.hosts.get(host, {}).get("mac")

    def get_host_ip(self, host: str) -> Optional[str]:
        return self.hosts.get(host, {}).get("ip")

    def get_switch_for_host(self, host: str) -> Optional[str]:
        return self.hosts.get(host, {}).get("switch")

    def get_all_links(self) -> List[str]:
        """Returns list of normalized link names (e.g., 's1-s2')"""
        all_links = set()
        for link in self.links:
            nodes = sorted([link['src'], link['dst']])
            all_links.add(f"{nodes[0]}-{nodes[1]}")
        return list(all_links)

    def is_link_available(self, u: str, v: str, excluded_links: Set[str]) -> bool:
        """Check if link u-v is usable (not in excluded_links)."""
        l1 = f"{u}-{v}"
        l2 = f"{v}-{u}"
        return l1 not in excluded_links and l2 not in excluded_links

    def shortest_path(self, src: str, dst: str, excluded_links: List[str] = None) -> List[str]:
        """
        Find shortest path from src switch to dst switch using BFS.
        Returns list of switches [src, ..., dst] or empty list if no path.
        """
        if excluded_links is None:
            excluded_links = []
        excluded_set = set(excluded_links)

        if src == dst:
            return [src]

        visited = {src}
        queue = deque([[src]])

        while queue:
            path = queue.popleft()
            node = path[-1]

            for neighbor in self.adj.get(node, []):
                if neighbor not in visited and self.is_link_available(node, neighbor, excluded_set):
                    new_path = list(path)
                    new_path.append(neighbor)
                    if neighbor == dst:
                        return new_path
                    visited.add(neighbor)
                    queue.append(new_path)
        return []

    def k_shortest_paths(self, src: str, dst: str, k: int = 3, excluded_links: List[str] = None) -> List[List[str]]:
        """
        Find up to k shortest paths using a simple BFS that allows multiple visits
        (but no cycles in a single path).
        """
        if excluded_links is None:
            excluded_links = []
        excluded_set = set(excluded_links)
        
        paths = []
        queue = deque([[src]])
        
        while queue and len(paths) < k:
            path = queue.popleft()
            node = path[-1]
            
            if node == dst:
                paths.append(path)
                continue
                
            for neighbor in self.adj.get(node, []):
                # Avoid cycles in the current path
                if neighbor not in path and self.is_link_available(node, neighbor, excluded_set):
                    new_path = list(path)
                    new_path.append(neighbor)
                    queue.append(new_path)
                    
        return paths

    def get_mac_to_ip_map(self) -> Dict[str, str]:
        """Helper for POX controller ARP proxy."""
        return {h["mac"]: h["ip"] for h in self.hosts.values()}
        
    def get_ip_to_mac_map(self) -> Dict[str, str]:
        """Helper for POX controller ARP proxy."""
        return {h["ip"]: h["mac"] for h in self.hosts.values()}
