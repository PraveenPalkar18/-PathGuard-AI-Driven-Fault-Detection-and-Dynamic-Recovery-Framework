#!/usr/bin/env bash
# ==============================================================================
# PathGuard: One-Click Automated Final Demo Launcher
# ==============================================================================
# Usage: sudo ./demo/run_final_demo.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Project root directory derivation
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Cleanup trap to guarantee all background processes terminate on Ctrl+C or exit
cleanup() {
    echo -e "\n${YELLOW}[$(date +'%H:%M:%S')]${NC} 🧹 Cleaning up background services..."
    pkill -f "python3.*final_demo.py" 2>/dev/null || true
    pkill -f "python3.*app.py" 2>/dev/null || true
    pkill -f "pox.py" 2>/dev/null || true
    fuser -k 5000/tcp > /dev/null 2>&1 || true
    fuser -k 8080/tcp > /dev/null 2>&1 || true
    fuser -k 6633/tcp > /dev/null 2>&1 || true
    echo -e "${GREEN}[$(date +'%H:%M:%S')] ✓ Cleanup complete${NC}"
}

trap cleanup EXIT

# ── Header ────────────────────────────────────────────────────────────
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}   🛡️  PATHGUARD: AI-DRIVEN SDN SELF-HEALING FINAL EXAM DEMO         ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Verify running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This launcher must be run with root privileges.${NC}"
    echo -e "Please run using: ${BOLD}sudo ./demo/run_final_demo.sh${NC}"
    exit 1
fi

# ── Prepare Directories and Logs ──────────────────────────────────────
echo -e "${BLUE}[1/5] Preparing environment and clearing old timeline events...${NC}"
mkdir -p "$PROJECT_ROOT/results"
rm -f "$PROJECT_ROOT/results/"*.log
rm -f "$PROJECT_ROOT/results/"*.json
rm -f "$PROJECT_ROOT/datasets/network_data.csv"
echo -e "${GREEN}✓ Environment ready${NC}\n"

# ── Start POX Controller ──────────────────────────────────────────────
echo -e "${BLUE}[2/5] Starting POX SDN Controller in background...${NC}"
cd "$PROJECT_ROOT"
# Launch POX with pathguard component and Web REST service on port 8080
./controller/run_pox.sh > "$PROJECT_ROOT/results/pox.log" 2>&1 &

echo -e "      Waiting for POX to bind OpenFlow port 6633..."
until nc -z 127.0.0.1 6633 2>/dev/null; do
    sleep 0.5
done
echo -e "${GREEN}✓ POX controller online (OpenFlow 6633, Web REST API 8080)${NC}\n"

# ── Start Flask Dashboard ─────────────────────────────────────────────
echo -e "${BLUE}[3/5] Starting Flask Web Dashboard...${NC}"
sudo -u wifi python3 dashboard/app.py > "$PROJECT_ROOT/results/dashboard.log" 2>&1 &

echo -e "      Waiting for Dashboard to bind web server port 5000..."
DASHBOARD_READY=0
for i in {1..60}; do
    if nc -z 127.0.0.1 5000 2>/dev/null; then
        DASHBOARD_READY=1
        break
    fi
    sleep 0.5
done

if [ "$DASHBOARD_READY" -eq 1 ]; then
    echo -e "${GREEN}✓ Web Dashboard online at http://localhost:5000${NC}\n"
else
    echo -e "${RED}⚠ Warning: Dashboard port 5000 not responding. Please check: results/dashboard.log${NC}\n"
fi

# ── Display Presentation Guidelines ───────────────────────────────────
echo -e "${CYAN}======================================================================${NC}"
echo -e "${BOLD}🎯 PRESENTATION INSTRUCTIONS FOR FINAL EXAM PANEL:${NC}"
echo -e "  1. Open Chrome/Firefox to: ${BOLD}${GREEN}http://localhost:5000${NC}"
echo -e "  2. Keep this terminal side-by-side with your browser."
echo -e "  3. The network status, topology map, and severity alerts will update"
echo -e "     in real time as the automated script drives through all phases."
echo -e "  4. Highlight the following transitions to the professors:"
echo -e "     • ${GREEN}Green (NORMAL)${NC}: All nodes healthy, high baseline healthscore."
echo -e "     • ${YELLOW}Yellow (WARNING)${NC}: Access segment s4-s8 degrades, RTT jitter increases."
echo -e "     • ${RED}Red (CRITICAL)${NC}: Link s4-s8 fails completely, health score drops to 0."
echo -e "     • ${BLUE}Blue (RECOVERING)${NC}: AI triggers self-healing, reroutes h1 ➔ h24."
echo -e "     • ${GREEN}Green (RESTORED)${NC}: Core link repaired, baseline mesh flows restored."
echo -e "${CYAN}======================================================================${NC}"
echo ""

read -p "▶️  Press [ENTER] to start the automated 6-phase Mininet execution..."

# ── Launch Final Demo Orchestration ───────────────────────────────────
echo -e "\n${BLUE}[5/5] Executing automated SDN fault-detection and recovery lifecycle...${NC}"
echo "----------------------------------------------------------------------"
sudo python3 "$PROJECT_ROOT/demo/final_demo.py"
echo "----------------------------------------------------------------------"

echo -e "${GREEN}🎉 Demo completed successfully! All processes cleanly shut down.${NC}\n"
