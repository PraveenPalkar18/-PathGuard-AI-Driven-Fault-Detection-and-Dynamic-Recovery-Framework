#!/usr/bin/env python3
"""
PathGuard POX SDN Controller
----------------------------
Replaces l2_learning and Spanning Tree with dynamic shortest-path routing
using the TopoGraph utility.
Exposes a /reroute HTTP API to dynamically inject OpenFlow rules upon failure.
Handles ARP locally to eliminate broadcast loops in the hierarchical/mesh topology.

Verification Steps:
# Step 1: Start controller
./controller/run_pox.sh

# Step 2: Start topology
sudo python3 topology/topology.py

# Step 3: Verify normal forwarding
# Inside Mininet: pingall (should be 0% drop)

# Step 4: Test REST failover manually
curl -X POST -H "Content-Type: application/json" \\
     -d '{"failed_links": ["s1-s2"]}' \\
     http://127.0.0.1:8080/reroute

# Step 5: Verify recovery
curl -X POST -H "Content-Type: application/json" \\
     -d '{"failed_links": []}' \\
     http://127.0.0.1:8080/reroute
"""

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.util import dpid_to_str
from pox.lib.addresses import IPAddr, EthAddr
from pox.lib.packet.ethernet import ethernet
from pox.lib.packet.arp import arp
from pox.web.webcore import SplitRequestHandler
import json
import sys
import os

log = core.getLogger()

# Ensure project root is in path so we can import topology
# Prefer explicit PATHGAURD_ROOT, otherwise derive from this file's parent directories
project_root = os.environ.get("PATHGAURD_ROOT")
if not project_root:
    for candidate in [
        "/home/wifi/pathgaurd",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    ]:
        if os.path.isdir(os.path.join(candidate, "topology")):
            project_root = candidate
            break
    if not project_root:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from topology.topo_graph import TopoGraph
except ImportError:
    log.error("Failed to import TopoGraph! Make sure controller is run with access to project root.")
    TopoGraph = None

class RerouteHandler(SplitRequestHandler):
    """
    Handles REST requests at /reroute
    Expected format: POST {"failed_links": ["s1-s2", "s7-s1"]}
    Backward compatibility: POST {"failed_link": "s1-s2"}
    """
    # Disable POX CookieGuard to prevent modern Python 3 bytes/str crashes in webcore
    pox_cookieguard = False

    def do_POST(self):
        try:
            length = int(self.headers.get('content-length', 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            
            failed_links = data.get("failed_links", [])
            # Backward compatibility
            if "failed_link" in data and data["failed_link"]:
                failed_links.append(data["failed_link"])
                
            log.info(f"📥 Received Dynamic Reroute request for links: {failed_links}")

            # CRITICAL FIX: Run enforce_scenario in a daemon thread so the HTTP
            # response is returned immediately. enforce_scenario calls push_switch_rules
            # for all 12 switches sequentially which takes >10s and caused urllib
            # to time out in recover.py, silently dropping the entire reroute command.
            import threading as _threading
            _threading.Thread(
                target=core.PathGuardController.enforce_scenario,
                args=(failed_links,),
                name="pathguard-enforce",
                daemon=True
            ).start()
            
            if not failed_links:
                response = {"status": "restored"}
            else:
                response = {"status": "success", "applied_links": failed_links}
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            log.error(f"❌ Error in REST handler: {e}")
            self.send_response(500)
            self.end_headers()


class PathGuardController(object):
    def __init__(self):
        self.connections = {}
        self.current_failed_links = []
        
        # Load topology graph
        if TopoGraph:
            self.topo = TopoGraph()
            self.ip_to_mac = self.topo.get_ip_to_mac_map()
            log.info(f"🗺️ Loaded topology with {len(self.topo.switches)} switches and {len(self.topo.hosts)} hosts")
        else:
            log.error("❌ TopoGraph not available!")
            self.topo = None
            self.ip_to_mac = {}
            
        core.openflow.addListeners(self)
        log.info("🛡️ PathGuard Controller Module Initialized")

    def _handle_ConnectionUp(self, event):
        dpid = event.dpid
        self.connections[dpid] = event.connection
        log.info(f"🔌 Switch Connection Established: s{dpid} (DPID={dpid})")
        
        # When a connection comes up, push our current scenario rules to it
        self.push_switch_rules(dpid, self.current_failed_links)

    def _handle_ConnectionDown(self, event):
        dpid = event.dpid
        if dpid in self.connections:
            del self.connections[dpid]
        log.info(f"🔌 Switch Disconnected: s{dpid}")

    def enforce_scenario(self, failed_links):
        """Reprogram all connected switches with the target scenario."""
        self.current_failed_links = failed_links
        log.info(f"⚙️ Reprogramming all switches for failed links: {failed_links or 'NONE (NORMAL)'}")
        for dpid in list(self.connections.keys()):
            self.push_switch_rules(dpid, failed_links)

    def compute_forwarding_table(self, dpid_str, failed_links):
        """
        Computes MAC -> out_port rules for a specific switch by calculating 
        shortest paths to all known hosts, avoiding failed_links.
        """
        rules = {}
        if not self.topo:
            return rules

        for host_name, host_info in self.topo.hosts.items():
            dst_mac = host_info['mac']
            target_switch = host_info['switch']
            host_port = host_info['port']
            
            # If the destination host is connected directly to this switch
            if target_switch == dpid_str:
                rules[dst_mac] = host_port
                continue
                
            # Find shortest path from this switch to target switch
            path = self.topo.shortest_path(dpid_str, target_switch, failed_links)
            if path and len(path) > 1:
                next_hop_switch = path[1]
                out_port = self.topo.get_port(dpid_str, next_hop_switch)
                if out_port:
                    rules[dst_mac] = out_port
            else:
                pass # No path available
                
        return rules

    def push_switch_rules(self, dpid, failed_links):
        """Clears old rules and pushes dynamic shortest-path flows to a switch."""
        connection = self.connections.get(dpid)
        if not connection:
            return

        dpid_str = f"s{dpid}"

        # 1. Flush old dynamic flows on the switch
        clear_msg = of.ofp_flow_mod(command=of.OFPFC_DELETE)
        connection.send(clear_msg)
        
        # 2. Compute dynamic rules for this switch avoiding failed_links
        rules = self.compute_forwarding_table(dpid_str, failed_links)
        
        # 3. Install flows
        for dest_mac, out_port in rules.items():
            msg = of.ofp_flow_mod()
            msg.match = of.ofp_match(dl_dst=EthAddr(dest_mac))
            msg.actions.append(of.ofp_action_output(port=out_port))
            msg.idle_timeout = of.OFP_FLOW_PERMANENT
            msg.hard_timeout = of.OFP_FLOW_PERMANENT
            connection.send(msg)
            
        log.info(f"✅ Updated flow rules injected successfully into {dpid_str} ({len(rules)} rules)")

    def _handle_PacketIn(self, event):
        """
        Catches packets that didn't match flow table.
        We intercept ARP requests and respond as an ARP proxy, preventing broadcast loops!
        """
        packet = event.parsed
        if not packet.parsed:
            return

        if packet.type == ethernet.ARP_TYPE:
            arp_packet = packet.payload
            if arp_packet.opcode == arp.REQUEST:
                target_ip = str(arp_packet.protodst)
                
                # Lookup dynamic MAC from TopoGraph
                target_mac = self.ip_to_mac.get(target_ip)
                if target_mac:
                    log.debug(f"💡 ARP Proxy: Resolving {target_ip} to {target_mac} for host at s{event.dpid}")
                    
                    # Craft ARP Reply
                    arp_reply = arp()
                    arp_reply.hwsrc = EthAddr(target_mac)
                    arp_reply.hwdst = arp_packet.hwsrc
                    arp_reply.protosrc = arp_packet.protodst
                    arp_reply.protodst = arp_packet.protosrc
                    arp_reply.opcode = arp.REPLY
                    
                    # Wrap in Ethernet Frame
                    eth = ethernet()
                    eth.type = ethernet.ARP_TYPE
                    eth.src = EthAddr(target_mac)
                    eth.dst = packet.src
                    eth.payload = arp_reply
                    
                    # PacketOut via same port it arrived on
                    msg = of.ofp_packet_out()
                    msg.data = eth.pack()
                    msg.actions.append(of.ofp_action_output(port=event.port))
                    event.connection.send(msg)


def launch():
    """
    Standard POX component launcher.
    Registers the core controller and binds the API listener.
    """
    # Register Core Component
    controller = PathGuardController()
    core.register("PathGuardController", controller)
    
    # Register with core web server
    def startup():
        if hasattr(core, 'WebServer'):
            log.info("🌐 Exposing /reroute REST API endpoint on POX Web Server")
            core.WebServer.set_handler("/reroute", RerouteHandler)
        else:
            log.error("❌ POX Web Server component not loaded! Ensure 'web' is loaded.")
            
    core.call_when_ready(startup, ["WebServer"])

