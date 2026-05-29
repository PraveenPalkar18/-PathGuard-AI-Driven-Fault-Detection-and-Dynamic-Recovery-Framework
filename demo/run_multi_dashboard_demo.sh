#!/usr/bin/env bash
# ==============================================================================
# PathGuard: Multi-Dashboard Presentation Mode Launcher
# ==============================================================================
# Starts 6 dashboards simultaneously for professional viva/demo presentation:
#
#   PORT 5000  →  REAL live system (untouched: ML + Mininet + OpenFlow + BFS)
#   PORT 5001  →  NORMAL snapshot    (healthy green topology)
#   PORT 5002  →  WARNING snapshot   (congested yellow topology)
#   PORT 5003  →  CRITICAL snapshot  (failed red link)
#   PORT 5004  →  RECOVERING snapshot (BFS rerouting orange)
#   PORT 5005  →  RECOVERED snapshot  (blue bypass active)
#
# Snapshot dashboards show REAL captured states from actual system runs.
# The real system on port 5000 is never modified.
#
# Usage:
#   # Step 1: Capture real snapshots (run once, needs sudo + Mininet):
#   sudo ./demo/run_real_fast_demo.sh
#
#   # Step 2: Start all presentation dashboards (no sudo needed):
#   ./demo/run_multi_dashboard_demo.sh
#
#   # OR: Start snapshot dashboards only (if real system already on 5000):
#   ./demo/run_multi_dashboard_demo.sh --snapshots-only
#
#   # With live snapshot refresh (updates dashboards as new snapshots arrive):
#   ./demo/run_multi_dashboard_demo.sh --live-refresh
# ==============================================================================

set -euo pipefail

# ── Parse args ────────────────────────────────────────────────────────────────
SNAPSHOTS_ONLY=0
LIVE_REFRESH=0
NO_BROWSER=0
NO_REAL=0

for arg in "$@"; do
    case "$arg" in
        --snapshots-only) SNAPSHOTS_ONLY=1 ;;
        --live-refresh)   LIVE_REFRESH=1 ;;
        --no-browser)     NO_BROWSER=1 ;;
        --no-real)        NO_REAL=1 ;;
    esac
done

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ACTUAL_USER="${SUDO_USER:-${USER:-wifi}}"

# ── Cleanup trap ─────────────────────────────────────────────────────────────
cleanup() {
    echo -e "\n${YELLOW}[$(date +'%H:%M:%S')]${NC} 🧹 Stopping all dashboard servers..."
    pkill -f "demo_dashboards.py"  2>/dev/null || true
    pkill -f "snapshot_capture.py" 2>/dev/null || true
    # Only kill the real dashboard if WE started it
    if [ "${STARTED_REAL:-0}" -eq 1 ]; then
        pkill -f "python3.*app\.py"  2>/dev/null || true
    fi
    for port in 5001 5002 5003 5004 5005; do
        fuser -k ${port}/tcp >/dev/null 2>&1 || true
    done
    echo -e "${GREEN}✓ All demo servers stopped.${NC}"
}
trap cleanup EXIT

# ── Header ────────────────────────────────────────────────────────────────────
echo -e ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  🛡️  PathGuard Multi-Dashboard Presentation Mode                    ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Real ML • Real BFS • Real Snapshots • Professional Demo Setup      ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
echo -e ""

# ── [1/4] Check snapshot files ────────────────────────────────────────────────
echo -e "${BLUE}[1/4] Checking snapshot availability...${NC}"
DEMO_DIR="$PROJECT_ROOT/results/demo_states"
mkdir -p "$DEMO_DIR"

SNAPSHOTS_FOUND=0
for state in normal warning critical recovering recovered; do
    if [ -f "$DEMO_DIR/$state.json" ]; then
        SNAPSHOTS_FOUND=$((SNAPSHOTS_FOUND + 1))
        size=$(du -h "$DEMO_DIR/$state.json" 2>/dev/null | cut -f1)
        echo -e "  ${GREEN}✓${NC}  $state.json  ($size)"
    else
        echo -e "  ${YELLOW}⚠${NC}  $state.json  (not yet captured)"
    fi
done

if [ "$SNAPSHOTS_FOUND" -eq 0 ]; then
    echo -e ""
    echo -e "  ${YELLOW}⚠  No real snapshots found yet.${NC}"
    echo -e "  Demo dashboards will show pre-seeded placeholders until real snapshots are captured."
    echo -e ""
    echo -e "  ${BOLD}To capture real snapshots, run in another terminal:${NC}"
    echo -e "    ${CYAN}sudo ./demo/run_real_fast_demo.sh${NC}"
    echo -e ""
    echo -e "  Continuing with placeholder dashboards..."
elif [ "$SNAPSHOTS_FOUND" -lt 5 ]; then
    MISSING=$((5 - SNAPSHOTS_FOUND))
    echo -e ""
    echo -e "  ${YELLOW}$SNAPSHOTS_FOUND/5 snapshots found ($MISSING missing).${NC}"
    echo -e "  Missing states will show pre-seeded placeholders."
fi
echo -e ""

# ── [2/4] Start real dashboard (port 5000) ────────────────────────────────────
STARTED_REAL=0
if [ "$SNAPSHOTS_ONLY" -eq 0 ] && [ "$NO_REAL" -eq 0 ]; then
    echo -e "${BLUE}[2/4] Starting real PathGuard dashboard (port 5000)...${NC}"

    # Check if already running
    if nc -z 127.0.0.1 5000 2>/dev/null; then
        echo -e "  ${GREEN}✓ Real dashboard already running on :5000${NC}"
    else
        # Start as normal user to avoid permission issues with results/
        sudo -u "$ACTUAL_USER" python3 "$PROJECT_ROOT/dashboard/app.py" \
            > "$PROJECT_ROOT/results/dashboard.log" 2>&1 &
        STARTED_REAL=1

        for i in $(seq 1 20); do
            if nc -z 127.0.0.1 5000 2>/dev/null; then
                echo -e "  ${GREEN}✓ Real dashboard live on :5000${NC}"
                break
            fi
            if [ "$i" -eq 20 ]; then
                echo -e "  ${YELLOW}⚠ Dashboard not responding — check results/dashboard.log${NC}"
            fi
            sleep 0.5
        done
    fi
else
    echo -e "${BLUE}[2/4] Skipping real dashboard (--snapshots-only or --no-real)${NC}"
    if nc -z 127.0.0.1 5000 2>/dev/null; then
        echo -e "  ${GREEN}✓ Real dashboard already running on :5000${NC}"
    else
        echo -e "  ${YELLOW}⚠ Port 5000 not running — start it manually if needed${NC}"
    fi
fi
echo -e ""

# ── [3/4] Start snapshot capture watcher ─────────────────────────────────────
echo -e "${BLUE}[3/4] Starting real-time snapshot capture watcher...${NC}"
REFRESH_FLAG=""
if [ "$LIVE_REFRESH" -eq 1 ]; then
    REFRESH_FLAG="--live-refresh"
fi

sudo -u "$ACTUAL_USER" python3 "$PROJECT_ROOT/dashboard/snapshot_capture.py" \
    --poll 0.5 $REFRESH_FLAG \
    >> "$PROJECT_ROOT/results/snapshot_capture.log" 2>&1 &
CAPTURE_PID=$!
sleep 0.5

if kill -0 "$CAPTURE_PID" 2>/dev/null; then
    echo -e "  ${GREEN}✓ Snapshot watcher running (PID=$CAPTURE_PID)${NC}"
    echo -e "  ${CYAN}  Watching: results/runtime_state.json${NC}"
    echo -e "  ${CYAN}  Saving:   results/demo_states/{normal,warning,critical,recovering,recovered}.json${NC}"
    if [ "$LIVE_REFRESH" -eq 1 ]; then
        echo -e "  ${CYAN}  Mode: LIVE REFRESH — snapshots update as new real states arrive${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠ Snapshot watcher may have exited — demo dashboards still work with existing snapshots${NC}"
fi
echo -e ""

# ── [4/4] Start demo dashboards (ports 5001-5005) ────────────────────────────
echo -e "${BLUE}[4/4] Starting demo dashboards (ports 5001-5005)...${NC}"

DEMO_CMD="python3 $PROJECT_ROOT/dashboard/demo_dashboards.py --all"
if [ "$LIVE_REFRESH" -eq 1 ]; then
    DEMO_CMD="$DEMO_CMD --live-refresh"
fi

sudo -u "$ACTUAL_USER" $DEMO_CMD \
    >> "$PROJECT_ROOT/results/demo_dashboards.log" 2>&1 &
DEMO_PID=$!

# Wait for all 5 demo ports to bind
echo -e "  Waiting for demo servers to start..."
ALL_OK=1
for port in 5001 5002 5003 5004 5005; do
    ok=0
    for i in $(seq 1 20); do
        if nc -z 127.0.0.1 $port 2>/dev/null; then
            ok=1
            break
        fi
        sleep 0.3
    done
    if [ "$ok" -eq 1 ]; then
        echo -e "  ${GREEN}✓${NC}  http://localhost:$port"
    else
        echo -e "  ${YELLOW}⚠${NC}  http://localhost:$port  (may need a moment)"
        ALL_OK=0
    fi
done
echo -e ""

# ── Open browser tabs ────────────────────────────────────────────────────────
if [ "$NO_BROWSER" -eq 0 ] && command -v xdg-open &>/dev/null; then
    echo -e "  ${CYAN}Opening browser tabs...${NC}"
    URLS=("http://localhost:5000" "http://localhost:5001" "http://localhost:5002"
          "http://localhost:5003" "http://localhost:5004" "http://localhost:5005")
    for url in "${URLS[@]}"; do
        xdg-open "$url" 2>/dev/null &
        sleep 0.3
    done
    echo -e "  ${GREEN}✓ Browser tabs opened${NC}\n"
fi

# ── Presentation instructions ─────────────────────────────────────────────────
echo -e "${CYAN}════════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}🎯 PRESENTATION SETUP — All 6 dashboards ready:${NC}"
echo -e ""
echo -e "  ${BOLD}Screen/Tab 1 (LIVE):${NC}  ${GREEN}http://localhost:5000${NC}  ← Real AI + Mininet + OpenFlow"
echo -e "  ${BOLD}Tab 2 (NORMAL):${NC}       ${GREEN}http://localhost:5001${NC}  🟢 Healthy green topology"
echo -e "  ${BOLD}Tab 3 (WARNING):${NC}      ${YELLOW}http://localhost:5002${NC}  🟡 Congested yellow link"
echo -e "  ${BOLD}Tab 4 (CRITICAL):${NC}     ${RED}http://localhost:5003${NC}  🔴 Failed link (s4-s8 down)"
echo -e "  ${BOLD}Tab 5 (RECOVERING):${NC}   http://localhost:5004  🟠 BFS rerouting active"
echo -e "  ${BOLD}Tab 6 (RECOVERED):${NC}    ${BLUE}http://localhost:5005${NC}  🔵 Bypass path live"
echo -e ""
echo -e "  ${BOLD}Snapshot proof:${NC}  results/demo_states/*.json"
echo -e "  ${BOLD}Capture source:${NC}  results/runtime_state.json (written by real monitor.py)"
echo -e ""
echo -e "  ${BOLD}To capture NEW real snapshots:${NC}"
echo -e "    ${CYAN}sudo ./demo/run_real_fast_demo.sh${NC}  (in a separate terminal)"
echo -e ""
echo -e "  ${BOLD}To view snapshot proof:${NC}"
echo -e "    ${DIM:-}python3 dashboard/snapshot_capture.py --status${NC}"
echo -e ""
if [ "$LIVE_REFRESH" -eq 1 ]; then
    echo -e "  ${CYAN}🔄 LIVE REFRESH enabled — snapshot dashboards auto-update${NC}"
fi
echo -e "${CYAN}════════════════════════════════════════════════════════════════════${NC}"
echo -e ""
echo -e "  Press ${BOLD}Ctrl+C${NC} to stop all dashboard servers."
echo -e ""

# ── Keep alive ────────────────────────────────────────────────────────────────
while true; do
    sleep 10
    # Check if demo dashboard process is still alive
    if ! kill -0 "$DEMO_PID" 2>/dev/null; then
        echo -e "${YELLOW}[$(date +'%H:%M:%S')] Demo dashboard process stopped — restarting...${NC}"
        sudo -u "$ACTUAL_USER" $DEMO_CMD \
            >> "$PROJECT_ROOT/results/demo_dashboards.log" 2>&1 &
        DEMO_PID=$!
    fi
done
