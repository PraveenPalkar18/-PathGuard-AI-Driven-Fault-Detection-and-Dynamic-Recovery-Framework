#!/usr/bin/env python3
"""
PathGuard POX SDN Controller
----------------------------
Replaces l2_learning and Spanning Tree with deterministic static-routing.
Exposes a /reroute HTTP API to dynamically inject OpenFlow rules upon failure.
Handles ARP locally to eliminate broadcast loops in the triangle topology.

Verification Steps:
# Step 1: Start controller
./controller/run_pox.sh

# Step 2: Start topology
sudo python3 topology/topology.py

# Step 3: Verify normal forwarding
# Inside Mininet: pingall (should be 0% drop)

# Step 4: Test REST failover manually
curl -X POST -H "Content-Type: application/json" \
     -d '{"failed_link": "s1-s2"}' \
     http://127.0.0.1:8080/reroute

# Step 5: Verify recovery
curl -X POST -H "Content-Type: application/json" \
     -d '{"failed_link": null}' \
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

log = core.getLogger()

# Deterministic network mappings
IP_TO_MAC = {
    "10.0.0.1": "00:00:00:00:00:01",
    "10.0.0.2": "00:00:00:00:00:02",
    "10.0.0.3": "00:00:00:00:00:03",
    "10.0.0.4": "00:00:00:00:00:04",
}

def get_scenario_mapping(failed_link=None):
    """
    Returns output ports for each switch (dpid) and destination MAC.
    Topology Layout:
      s1: h1=port 1, h2=port 2, s2=port 3, s3=port 4
      s2: h4=port 1, s1=port 2, s3=port 3
      s3: h3=port 1, s2=port 2, s1=port 3
    """
    # BASE MAP (Normal state)
    mapping = {
        1: { # s1
            "00:00:00:00:00:01": 1, # direct to h1
            "00:00:00:00:00:02": 2, # direct to h2
            "00:00:00:00:00:03": 4, # s1 -> s3
            "00:00:00:00:00:04": 3, # s1 -> s2
        },
        2: { # s2
            "00:00:00:00:00:01": 2, # s2 -> s1
            "00:00:00:00:00:02": 2, # s2 -> s1
            "00:00:00:00:00:03": 3, # s2 -> s3
            "00:00:00:00:00:04": 1, # direct to h4
        },
        3: { # s3
            "00:00:00:00:00:01": 3, # s3 -> s1
            "00:00:00:00:00:02": 3, # s3 -> s1
            "00:00:00:00:00:03": 1, # direct to h3
            "00:00:00:00:00:04": 2, # s3 -> s2
        }
    }

    # FAILOVER MODIFICATIONS
    if failed_link == "s1-s2":
        log.info("⚠️ Applying s1-s2 Down: Rerouting through s1 <-> s3 <-> s2")
        mapping[1]["00:00:00:00:00:04"] = 4 # s1 sends h4 traffic to s3
        mapping[2]["00:00:00:00:00:01"] = 3 # s2 sends h1 traffic to s3
        mapping[2]["00:00:00:00:00:02"] = 3 # s2 sends h2 traffic to s3
    elif failed_link == "s2-s3":
        log.info("⚠️ Applying s2-s3 Down: Rerouting through s2 <-> s1 <-> s3")
        mapping[2]["00:00:00:00:00:03"] = 2 # s2 sends h3 traffic to s1
        mapping[3]["00:00:00:00:00:04"] = 3 # s3 sends h4 traffic to s1
    elif failed_link == "s1-s3":
        log.info("⚠️ Applying s1-s3 Down: Rerouting through s1 <-> s2 <-> s3")
        mapping[1]["00:00:00:00:00:03"] = 3 # s1 sends h3 traffic to s2
        mapping[3]["00:00:00:00:00:01"] = 2 # s3 sends h1 traffic to s2
        mapping[3]["00:00:00:00:00:02"] = 2 # s3 sends h2 traffic to s2

    return mapping


class RerouteHandler(SplitRequestHandler):
    """
    Handles REST requests at /reroute
    Expected format: POST {"failed_link": "s1-s2"}
    """
    # Disable POX CookieGuard to prevent modern Python 3 bytes/str crashes in webcore
    pox_cookieguard = False

    def do_POST(self):
        try:
            length = int(self.headers.get('content-length', 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            
            failed_link = data.get("failed_link", None)
            log.info(f"📥 Received Dynamic Reroute request for: {failed_link}")
            
            # Trigger controller to push rules
            core.PathGuardController.enforce_scenario(failed_link)
            
            if failed_link is None:
                response = {"status": "restored"}
            else:
                response = {"status": "success", "applied_link": failed_link}
                
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
        self.current_scenario = None
        core.openflow.addListeners(self)
        log.info("🛡️ PathGuard Controller Module Initialized")

    def _handle_ConnectionUp(self, event):
        dpid = event.dpid
        self.connections[dpid] = event.connection
        log.info(f"🔌 Switch Connection Established: s{dpid} (DPID={dpid})")
        
        # When a connection comes up, push our current scenario rules to it
        self.push_switch_rules(dpid, self.current_scenario)

    def _handle_ConnectionDown(self, event):
        dpid = event.dpid
        if dpid in self.connections:
            del self.connections[dpid]
        log.info(f"🔌 Switch Disconnected: s{dpid}")

    def enforce_scenario(self, failed_link):
        """Reprogram all connected switches with the target scenario."""
        self.current_scenario = failed_link
        log.info(f"⚙️ Reprogramming all switches for scenario: {failed_link or 'NORMAL'}")
        for dpid in list(self.connections.keys()):
            self.push_switch_rules(dpid, failed_link)

    def push_switch_rules(self, dpid, failed_link):
        """Clears old rules and pushes deterministic flow mods to a specific switch."""
        connection = self.connections.get(dpid)
        if not connection:
            return

        # 1. Flush old dynamic flows on the switch
        clear_msg = of.ofp_flow_mod(command=of.OFPFC_DELETE)
        connection.send(clear_msg)
        
        # 2. Install Scenario Maps
        mapping = get_scenario_mapping(failed_link)
        switch_ports = mapping.get(dpid, {})
        
        for dest_mac, out_port in switch_ports.items():
            # Target destination MAC rule
            msg = of.ofp_flow_mod()
            msg.match = of.ofp_match(dl_dst=EthAddr(dest_mac))
            msg.actions.append(of.ofp_action_output(port=out_port))
            msg.idle_timeout = of.OFP_FLOW_PERMANENT
            msg.hard_timeout = of.OFP_FLOW_PERMANENT
            connection.send(msg)
            
        log.info(f"✅ Updated flow rules injected successfully into s{dpid}")

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
                source_ip = str(arp_packet.protosrc)
                
                # Lookup deterministic MAC
                target_mac = IP_TO_MAC.get(target_ip)
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
