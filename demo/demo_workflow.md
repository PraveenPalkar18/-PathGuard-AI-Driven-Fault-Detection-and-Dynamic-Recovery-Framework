# PathGuard: Full Demonstration Workflow

This workflow details the systematic sequence to execute and verify the **PathGuard** AI-Driven Fault Detection and Dynamic Recovery Framework.

## Prerequisites
Ensure **Mininet**, **POX**, and the Python packages in requirements are installed.

---

## Full-System Run Workflow

### Phase 1: Automated Demo Execution
The fastest way to experience the complete pipeline is to launch the one-click demo script from the project root.

```bash
cd ~/pathgaurd
./run_demo.sh
```

#### What this script does automatically:
1. Spins up the **POX Controller** deploying the custom `pathguard_controller` module.
2. Launches the **Flask Web Dashboard** (`http://localhost:5000`).
3. Boots the **PathGuard 3-Switch Topology** and starts the **Real-Time AI Monitor** in the background.
4. Sequentially triggers network fault injects (link degradations and link down events) to showcase dynamic recovery.

---

### Phase 2: Manual Verification Steps
To inspect components individually, execute the commands in separate terminal tabs:

#### 1. Start the SDN Controller
```bash
cd ~/pathgaurd
./controller/run_pox.sh
```
*Expected behavior:* POX starts, loads `webcore` on port 8000, and binds the `/reroute` REST handler.

#### 2. Launch the Dashboard
```bash
cd ~/pathgaurd
python3 dashboard/app.py
```
*Expected behavior:* Flask server binds to `http://0.0.0.0:5000`. Open this in your browser to view the live SVG heatmap.

#### 3. Boot the Network with AI Monitoring Enabled
```bash
cd ~/pathgaurd
sudo python3 topology/topology.py --monitor
```
*Expected behavior:* Mininet builds the 3-switch full-mesh topology. The monitoring loop automatically loads the Random Forest model `ai/model.pkl` and prints active telemetry.

---

## System Integrity Checks

During a fault event, verify the following lifecycle elements:

1. **AI Classification**: The CLI prints `🚨 CRITICAL DETECTED` or `⚠️ WARNING DETECTED` with explanations like *"Severe packet loss observed"*.
2. **Timeline Persistent Logging**: Inspect `results/events.log` to verify historical timestamps of events.
3. **Adaptive Monitoring**: The console outputs show intervals automatically speeding up from 10s down to 1s during critical events.
4. **Network Health Scoring**: The Dashboard Header visually maps health from `100 (Healthy)` to `<60 (Critical)`.
5. **Dynamic Path Failover**: Observe the SVG heatmap in the Dashboard dynamically turning down links to **Red**, the Monitor REST call pushing explicit Flow Mods, and physical ICMP pings verifying restorations.
