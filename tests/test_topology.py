#!/usr/bin/env python3
"""
Test Suite: Topology Correctness
Verifies the TopoGraph loaded from port_map.json defines the expected
12 switches, 24 hosts, and contains expected path redundancies.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from topology.topo_graph import TopoGraph

def test_switch_and_host_counts():
    """Verify 12 switches and 24 hosts exist in topology."""
    topo = TopoGraph()
    assert len(topo.switches) == 12
    assert len(topo.hosts) == 24
    
    # Verify switch names s1-s12
    for i in range(1, 13):
        assert f"s{i}" in topo.switches
        
    # Verify host names h1-h24
    for i in range(1, 25):
        assert f"h{i}" in topo.hosts

def test_topo_graph_mappings():
    """Verify IP, MAC, and switch-association lookups."""
    topo = TopoGraph()
    
    # Check host 1 details
    assert topo.get_host_ip("h1") == "10.0.1.1"
    assert topo.get_host_mac("h1") == "00:00:00:00:00:01"
    assert topo.get_switch_for_host("h1") == "s8"
    assert topo.get_port("s8", "h1") == 3
    
    # Check host 24 details
    assert topo.get_host_ip("h24") == "10.0.5.4"
    assert topo.get_host_mac("h24") == "00:00:00:00:00:18"
    assert topo.get_switch_for_host("h24") == "s12"
    assert topo.get_port("s12", "h24") == 6

def test_adjacency_list():
    """Verify switch adjacency list matches port map."""
    topo = TopoGraph()
    # s1 is connected to s2, s3, s4, s5, s7
    assert sorted(topo.adj["s1"]) == sorted(["s2", "s3", "s4", "s5", "s7"])

def test_redundant_paths():
    """Verify shortest path routing and redundancy."""
    topo = TopoGraph()
    
    # Path from access s8 to access s12
    path = topo.shortest_path("s8", "s12")
    assert len(path) > 0
    assert path[0] == "s8"
    assert path[-1] == "s12"
    
    # Find up to 3 shortest paths
    k_paths = topo.k_shortest_paths("s8", "s12", k=3)
    assert len(k_paths) >= 2  # There should be multiple alternate routes
    for p in k_paths:
        assert p[0] == "s8"
        assert p[-1] == "s12"

def test_path_routing_under_failures():
    """Verify shortest path routing correctly bypasses excluded links."""
    topo = TopoGraph()
    
    # Primary shortest path s8 to s12 (normally s8->s4->s1->...)
    path_normal = topo.shortest_path("s8", "s12")
    
    # Let's say one of the intermediate links is down
    failed_link = f"{path_normal[1]}-{path_normal[2]}"
    path_recovered = topo.shortest_path("s8", "s12", excluded_links=[failed_link])
    
    assert len(path_recovered) > 0
    # The recovered path should be different from normal path
    assert path_recovered != path_normal
    
    # Verify no link on the recovered path uses the failed link
    for i in range(len(path_recovered) - 1):
        u, v = path_recovered[i], path_recovered[i+1]
        link_name = f"{min(u,v)}-{max(u,v)}"
        assert link_name != failed_link
