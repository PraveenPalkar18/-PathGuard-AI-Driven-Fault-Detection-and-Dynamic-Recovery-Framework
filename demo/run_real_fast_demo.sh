#!/usr/bin/env bash
# ==============================================================================
# PathGuard: One-Click REAL High-Speed Demo Launcher (Optimized)
# ==============================================================================
# Boots POX controller, Flask dashboard, then runs the real Mininet demo.
# ALL 6 phases use ACTUAL Mininet, ACTUAL ML inference, ACTUAL OpenFlow rules.
# runtime_state.json written ONLY by monitor.py and recovery engine — never fake.
#
# Usage:
#   sudo ./demo/run_real_fast_demo.sh           # Normal (with pause)
#   sudo ./demo/run_real_fast_demo.sh --no-pause # Skip Enter prompt (scripted)
#
# Target timing:
#   Phase 1 NORMAL:    ~5s  (3 baseline rounds at 1.3s/round)
#   Phase 2 WARNING:   ~5-8s (TURBO congestion: 25% loss + 60ms → AI detects)
#   Phase 3 CRITICAL:  ~3-5s (link cut → physical failsafe → 100% loss)
#   Phase 4+5 RECOV:   ~8-15s (BFS → POX flow_mod → 1.5s wait → verify ping)
#   Phase 6 RESTORED:  ~5-8s (link up → tc reset → AI reclassifies NORMAL)
#   Total:             ~26-41s (vs ~3min before optimization)
# ==============================================================================

set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────
NO_PAUSE=0
for arg in "$@"; do
    case "$arg" in
        --no-pause) NO_PAUSE=1 ;;
    esac
done

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Cleanup trap ─────────────────────────────────────────────────────────────
cleanup() {
    echo -e "\n${YELLOW}[$(date +'%H:%M:%S')]${NC} 🧹 Cleaning up background services..."
    pkill -f "python3.*real_fast_demo.py" 2>/dev/null || true
    pkill -f "python3.*fast_demo.py"      2>/dev/null || true
    pkill -f "python3.*app.py"            2>/dev/null || true
    pkill -f "pox.py"                     2>/dev/null || true
    killall iperf                         2>/dev/null || true
    mn -c                                 2>/dev/null || true
    fuser -k 5000/tcp  >/dev/null 2>&1    || true
    fuser -k 8080/tcp  >/dev/null 2>&1    || true
    fuser -k 6633/tcp  >/dev/null 2>&1    || true
    echo -e "${GREEN}[$(date +'%H:%M:%S')] ✓ Cleanup complete${NC}"
}
trap cleanup EXIT

# ── Header ────────────────────────────────────────────────────────────────────
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}   🛡️  PATHGUARD: REAL HIGH-SPEED SDN SELF-HEALING DEMO              ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}       Real Mininet • Real ML • Real OpenFlow • Real BFS             ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}       Zero fake state injection — runtime_state.json is real        ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Verify root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Must run as root (Mininet requires sudo).${NC}"
    echo -e "Run: ${BOLD}sudo ./demo/run_real_fast_demo.sh${NC}"
    exit 1
fi

# ── [1/5] Prepare environment ─────────────────────────────────────────────────
echo -e "${BLUE}[1/5] Preparing environment...${NC}"
cd "$PROJECT_ROOT"
mkdir -p "$PROJECT_ROOT/results"
# Fix permissions on results files if root-owned from previous runs
chmod 666 "$PROJECT_ROOT/results"/*.json 2>/dev/null || true
chmod 666 "$PROJECT_ROOT/results"/*.log  2>/dev/null || true
# Clear old state so dashboard starts fresh (real data starts immediately)
rm -f "$PROJECT_ROOT/results/runtime_state.json"
rm -f "$PROJECT_ROOT/results/recovery_metrics.json"
rm -f "$PROJECT_ROOT/datasets/network_data.csv"
echo -e "${GREEN}✓ Environment ready${NC}"

# ── Verify AI model ───────────────────────────────────────────────────────────
echo -e "  Checking AI model..."
if [ -f "$PROJECT_ROOT/ai/model.pkl" ]; then
    MODEL_SIZE=$(du -h "$PROJECT_ROOT/ai/model.pkl" | cut -f1)
    echo -e "  ${GREEN}✓ model.pkl found ($MODEL_SIZE)${NC}"
else
    echo -e "  ${YELLOW}⚠ model.pkl not found — training now...${NC}"
    if [ -f "$PROJECT_ROOT/datasets/network_data.csv" ]; then
        python3 "$PROJECT_ROOT/ai/train_model.py" || echo -e "  ${YELLOW}⚠ Training failed — will use heuristic fallback${NC}"
    else
        echo -e "  ${YELLOW}⚠ No dataset found — monitor will use heuristic fallback (still functional)${NC}"
    fi
fi
echo ""

# ── [2/5] Start POX Controller ────────────────────────────────────────────────
echo -e "${BLUE}[2/5] Starting POX SDN Controller (OpenFlow :6633, REST API :8080)...${NC}"
"$PROJECT_ROOT/controller/run_pox.sh" > "$PROJECT_ROOT/results/pox.log" 2>&1 &

echo -e "      Waiting for POX to bind port 6633..."
for i in $(seq 1 40); do
    if nc -z 127.0.0.1 6633 2>/dev/null; then
        echo -e "${GREEN}✓ POX online (OpenFlow :6633, REST :8080)${NC}"
        break
    fi
    if [ "$i" -eq 40 ]; then
        echo -e "${RED}✗ POX did not bind port 6633 within 20s — check results/pox.log${NC}"
        echo -e "  Last 10 lines of pox.log:"
        tail -10 "$PROJECT_ROOT/results/pox.log" 2>/dev/null || true
        exit 1
    fi
    sleep 0.5
done
echo ""

# ── [3/5] Start Flask Dashboard ───────────────────────────────────────────────
echo -e "${BLUE}[3/5] Starting Flask Dashboard (:5000)...${NC}"
# Run dashboard as the normal user (wifi) to avoid permission issues
ACTUAL_USER="${SUDO_USER:-wifi}"
sudo -u "$ACTUAL_USER" python3 "$PROJECT_ROOT/dashboard/app.py" \
    > "$PROJECT_ROOT/results/dashboard.log" 2>&1 &

echo -e "      Waiting for dashboard port 5000..."
DASH_OK=0
for i in $(seq 1 30); do
    if nc -z 127.0.0.1 5000 2>/dev/null; then
        DASH_OK=1
        break
    fi
    sleep 0.5
done

if [ "$DASH_OK" -eq 1 ]; then
    echo -e "${GREEN}✓ Dashboard live at ${BOLD}http://localhost:5000${NC}"
else
    echo -e "${YELLOW}⚠ Dashboard not responding on :5000 — check results/dashboard.log${NC}"
fi
echo ""

# ── [4/5] Instructions ────────────────────────────────────────────────────────
echo -e "${CYAN}════════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}🎯 PRESENTATION INSTRUCTIONS:${NC}"
echo -e "  1. Open browser → ${BOLD}${GREEN}http://localhost:5000${NC}"
echo -e "  2. Keep this terminal side-by-side with the browser."
echo -e "  3. Watch real AI-driven phase transitions:"
echo -e "     • ${GREEN}🟢 NORMAL${NC}:     ~5s   — RandomForest→NORMAL (health=100)"
echo -e "     • ${YELLOW}🟡 WARNING${NC}:   ~5-8s  — TURBO congestion (25% loss)→AI WARNING"
echo -e "     • ${RED}🔴 CRITICAL${NC}:  ~3-5s  — Link cut→physical down→100% loss→CRITICAL"
echo -e "     • ${CYAN}🟠 RECOVERING${NC}: immediate — BFS path ranking→POX flow_mod"
echo -e "     • ${BLUE}🔵 RECOVERED${NC}:  ~5-10s — bypass live→verify ping→RECOVERED"
echo -e "     • ${GREEN}🟢 RESTORED${NC}:  ~5-8s  — link up→tc reset→full-mesh→NORMAL"
echo -e ""
echo -e "  ${BOLD}Proof of real behavior after demo:${NC}"
echo -e "    ${BOLD}results/events.log${NC}  — AI predictions + BFS path scores + verify pings"
echo -e "    ${BOLD}results/pox.log${NC}     — real ofp_flow_mod messages from OVS switches"
echo -e ""
echo -e "  ${BOLD}Quick proof commands:${NC}"
echo -e "    grep 'AI predicted\\|PathRanker BFS\\|Verification' results/events.log | tail -20"
echo -e "    grep 'Updated flow rules\\|Reprogramming' results/pox.log | tail -10"
echo -e "${CYAN}════════════════════════════════════════════════════════════════════${NC}"
echo ""

if [ "$NO_PAUSE" -eq 0 ]; then
    read -rp "▶️  Press [ENTER] to start the real 6-phase PathGuard demo..."
fi

# ── [5/5] Run Real Demo ───────────────────────────────────────────────────────
echo -e "\n${BLUE}[5/5] Executing real AI-driven SDN demo...${NC}"
echo "──────────────────────────────────────────────────────────────────────"
DEMO_START=$(date +%s)
python3 "$PROJECT_ROOT/demo/real_fast_demo.py"
DEMO_END=$(date +%s)
DEMO_ELAPSED=$((DEMO_END - DEMO_START))
echo "──────────────────────────────────────────────────────────────────────"
echo -e "${GREEN}🎉 Real PathGuard demo complete! (${DEMO_ELAPSED}s total)${NC}"
echo ""
echo -e "  ${BOLD}View proof of real AI + SDN:${NC}"
echo -e "    grep 'AI predicted\\|PathRanker BFS\\|Selected optimal\\|Verification' \\"
echo -e "         $PROJECT_ROOT/results/events.log | tail -25"
echo ""
