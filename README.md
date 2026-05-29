# 🛡️ PathGuard: AI-Driven Fault Detection and Dynamic Recovery Framework

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Mininet](https://img.shields.io/badge/SDN-Mininet%20%2F%20Open%20vSwitch-brightgreen)](http://mininet.org/)
[![ML](https://img.shields.io/badge/Machine%20Learning-Scikit--Learn-orange)](https://scikit-learn.org/)
[![Framework](https://img.shields.io/badge/Dashboard-Flask%20%2F%20SVG%20Heatmap-lightgrey)](https://flask.palletsprojects.com/)
[![Tests](https://img.shields.io/badge/Testing-Pytest%20%2F%209%20Modules-success)](https://pytest.org/)

**PathGuard** is an advanced, production-grade Software-Defined Networking (SDN) framework that fuses high-speed parallel telemetry, multi-class Machine Learning classification, and deterministic controller routing into a fully closed-loop, self-healing network ecosystem.

By replacing traditional static, rule-based threshold configurations with a robust **explainable Random Forest classifier**, PathGuard isolates transient jitter from physical link degradations or cuts. Operating directly on the OpenFlow control plane via POX RESTful bindings, it triggers sub-second failover recovery, bypassing protocol convergence lag (Spanning Tree's ~40 seconds) to restore network stability in **< 400 milliseconds**.

---

## ✨ Key Features

* **🧠 Multi-Class AI Anomaly Classification**: Evaluates a 4-dimensional telemetry vector in real-time using a trained Random Forest model. Classifies the global network state into **🟢 NORMAL**, **🟡 WARNING** (congestion/jitter), and **🔴 CRITICAL** (link failures/cuts) with a Stratified 5-fold cross-validated accuracy of **99.4%**.
* **🛡️ Active Tomography Shield**: Incorporates dynamic tomographic tracing inside `fault_analyzer.py` to prevent "false-positive" link cuts on core channels during localized access-port congestion.
* **⚡ Confidence-Gated Self-Healing**: Implements a safety-gated routing mechanism that only triggers path rerouting if the predicted failure classification has a model confidence of $\ge 80\%$, preventing unstable routing oscillations.
* **🚀 Parallelized Telemetry Smoothing**: Leverages `ThreadPoolExecutor` and asynchronous standard namespaces pings via non-blocking standard shell subprocesses (`src.popen()`). The telemetry monitor cycle is reduced from **40s down to < 2s** and stabilized using a size-3 rolling smoothing window.
* **🔒 Loop-Free Full-Mesh controller ARP Proxying**: Runs a custom controller-based ARP proxy on the POX plane, eliminating packet-storm broadcast loops on full-mesh configurations without disabling redundant links.
* **🌌 Glassmorphic SVG Heatmap Visualization**: A responsive Flask web UI rendering glowing, interactive SVG topology graphs, live RTT charts, ML prediction scores, and explainable AI logs.

---

## 🛠️ Architecture & Topography

PathGuard supports a highly realistic, structured hierarchical **12-Switch, 24-Host SDN Network Topography**:

```text
                           [ Core Layer ]
                            s1 -- s2 -- s3
                           /  \  /  \  /  \
                          /    \/    \/    \
                         s4 --- s5 --- s6 --- s7  [ Distribution Layer ]
                        /  \   /  \   /  \   /  \
                       s8   s9    s10    s11    s12 [ Access Layer ]
                      /|\   /|\   /|\    /|\    /|\
                    Hosts  Hosts Hosts  Hosts  Hosts
                    h1-h5  h6-h10 h11-h15 h16-h20 h21-h24
```

### Decoupled Modular Layers:
1. **Emulated Data Plane**: Powered by Mininet & Open vSwitch (OVS). Links are dynamically throttled with queue delays or severed via link down procedures.
2. **OpenFlow Control Plane**: Custom POX SDN controller. Exposes atomic REST routing endpoints on port `8080` to inject `ofp_flow_mod` bypass rules on the fly.
3. **Telemetry & Monitor Daemon**: Background telemetry agent executing inside Mininet namespaces, writing real-time metrics to `results/runtime_state.json`.
4. **Machine Learning & Path Selection**: Random Forest predicts states; when gated criteria are met, the multi-factor scoring formula ranks alternative routes based on latency, hops, jitter, and packet loss.
5. **Real-time Web GUI**: Flask + D3.js frontend that displays real-time health transitions and autonomous self-healing states.

---

## 📦 Directory Layout

```text
pathgaurd/
├── topology/
│   ├── __init__.py
│   ├── topology.py           # Custom 12-switch 24-host topology generator
│   ├── topo_graph.py         # Network topology graph parser and paths resolver
│   └── port_map.json         # Physical switch interface maps
├── monitoring/
│   ├── monitor.py            # Concurrent Multi-threaded Telemetry Prober
│   ├── health.py             # Standardized 0-100 scoring formulation
│   ├── fault_analyzer.py     # Active Tomography Shield & link status resolver
│   └── runtime_state.py      # Local JSON telemetry writer
├── recovery/
│   ├── __init__.py
│   ├── path_selector.py      # Multi-factor metric ranking path evaluator
│   └── recover.py            # Autonomous Dynamic Restoration Engine
├── ai/
│   ├── train_model.py        # Machine Learning training & Stratified 5-Fold validation
│   └── model.pkl             # Serialized pre-trained RandomForest artifact
├── dashboard/
│   ├── app.py                # Flask REST Backend Provider & dynamic status server
│   ├── demo_dashboards.py    # Offline interactive demo simulator
│   ├── snapshot_capture.py   # State snapshots capture script
│   └── static/               # SVG JavaScript Heatmap & CSS
├── demo/
│   ├── final_demo.py         # 6-phase headless demo pipeline runner
│   ├── run_final_demo.sh     # One-click final demo launcher
│   ├── run_multi_dashboard_demo.sh # Live refresh multi-dashboard launcher
│   ├── run_real_fast_demo.sh # High-speed proof-of-concept launcher
│   └── demo_scenarios.py     # Fault injector scenario mappings
├── tests/                    # 9-module comprehensive testing suite
│   ├── test_ai_engine.py
│   ├── test_controller.py
│   ├── test_dashboard.py
│   ├── test_end_to_end.py
│   ├── test_fault_consistency.py
│   ├── test_fault_injection.py
│   ├── test_monitoring.py
│   ├── test_recovery.py
│   └── test_topology.py
├── results/
│   ├── demo_states/          # Standard states snapshots (normal, warning, etc.)
│   └── test_report.md        # Comprehensive verification report
├── run_demo.sh               # Baseline automated orchestrator
├── PATHGUARD_DEVELOPMENT_HANDBOOK.txt # 0-to-100 complete engineering manual
├── FINAL_EXAM_DEMO_GUIDE.md  # Live exam demonstration speaker script
└── PROJECT_PRESENTATION_GUIDE.txt # Class presentation slides reference
```

---

## 🚀 Getting Started & Demo Orchestration

### Prerequisite Dependencies:
Ensure Python 3.12+, Mininet, and Open vSwitch are installed. Then configure the python dependencies:
```bash
pip install pandas scikit-learn joblib flask requests pytest
```

### 🌟 1. The One-Click Live-Refresh Demo (Recommended)
To launch the complete environment (OVS cleanup, POX controller, Flask server, and the 6-phase automated fault injection timeline) in a single command:
```bash
cd ~/pathgaurd
sudo ./demo/run_multi_dashboard_demo.sh --live-refresh
```
Once initialized, open `http://localhost:5000` in your web browser. You can watch the network transition through its 6 lifecycle phases:
```mermaid
graph LR
    P1[🟢 Phase 1: NORMAL] --> P2[🟡 Phase 2: WARNING]
    P2 --> P3[🔴 Phase 3: CRITICAL]
    P3 --> P4[🟠 Phase 4: RECOVERING]
    P4 --> P5[🔵 Phase 5: RECOVERED]
    P5 --> P6[🟢 Phase 6: RESTORED]
```

### 🧪 2. Running Unit & Integration Tests
PathGuard has a robust testing suite comprising **9 separate modules** that test all elements from end-to-end routing to AI model inference consistency:
* To run the automated pytest suite:
  ```bash
  pytest tests/
  ```
* To execute the detailed, color-coded, phase-by-phase end-to-end lifecycle verification script:
  ```bash
  python3 tests/test_end_to_end.py
  ```

---

## 🎓 Academic Defense & Presentation Resources

* **`FINAL_EXAM_DEMO_GUIDE.md`**: Provides a complete, minute-by-minute live speaker script, visualization breakdowns, and model answers for professors' technical inquiries.
* **`PROJECT_PRESENTATION_GUIDE.txt`**: A slide-by-slide presenter's guide ready for classroom defense.
* **`PATHGUARD_DEVELOPMENT_HANDBOOK.txt`**: An exhaustive 400+ line technical handbook covering all architecture details, mathematical formulas, and telemetry smoothing layers.

---

## ⚖️ License
This research framework is published for academic prototyping and research evaluations in autonomous software-defined networks.

