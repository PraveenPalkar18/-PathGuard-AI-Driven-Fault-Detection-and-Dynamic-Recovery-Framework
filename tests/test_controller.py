#!/usr/bin/env python3
"""
Test Suite: POX SDN Controller Rules Calculation
Verifies that the PathGuardController compute_forwarding_table method
correctly resolves shortest-path MAC-to-port mappings under normal
and link-failure conditions.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Add POX home to path
sys.path.insert(0, "/home/wifi/pox")

# Mock POX components before import to prevent side-effects
import pox.core
pox.core.core = MagicMock()
pox.core.core.getLogger.return_value = MagicMock()

from controller.pathguard_controller import PathGuardController

def test_controller_normal_rules():
    """Verify flow table rules calculation under healthy conditions."""
    # Instantiating the controller will trigger TopoGraph load
    controller = PathGuardController()
    assert controller.topo is not None
    
    # Calculate rules for s8 under normal (healthy) conditions
    rules = controller.compute_forwarding_table("s8", [])
    
    # Verify that rules are computed for all 24 hosts
    assert len(rules) == 24
    
    # For h1 (connected directly to s8 on port 3), out_port must be 3
    h1_mac = "00:00:00:00:00:01"
    assert rules[h1_mac] == 3
    
    # For h6 (connected to s9), normal route goes s8 -> s4 -> s9
    # The output port on s8 towards s4 should be 1 (check port_map.json)
    h6_mac = "00:00:00:00:00:06"
    assert rules[h6_mac] == 1  # port 1 towards s4

def test_controller_failover_rules():
    """Verify flow rules correctly recalculate to bypass failed links."""
    controller = PathGuardController()
    
    # Normal rule for h6 from s8 is port 1 (towards s4)
    h6_mac = "00:00:00:00:00:06"
    normal_rules = controller.compute_forwarding_table("s8", [])
    assert normal_rules[h6_mac] == 1
    
    # Now inject failure on s8-s4 link
    failed_links = ["s8-s4"]
    failover_rules = controller.compute_forwarding_table("s8", failed_links)
    
    # For h1 (directly connected to s8), rule should still be port 3
    h1_mac = "00:00:00:00:00:01"
    assert failover_rules[h1_mac] == 3
    
    # For h6, route must now go s8 -> s5 -> s1 -> s4 -> s9 (or similar)
    # The output port on s8 towards s5 is port 2
    assert failover_rules[h6_mac] == 2  # bypassed failed link!
