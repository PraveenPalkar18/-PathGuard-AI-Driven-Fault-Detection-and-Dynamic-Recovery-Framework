#!/usr/bin/env python3
"""
PathGuard: Unified Fault Analysis Engine
-----------------------------------------
Reconciles health score, AI severity, link-level status, recovery state,
and contextual explanations so the dashboard stays logically consistent.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from monitoring.health import (
    calculate_health_score,
    get_health_label,
    NORMAL_HEALTH_MIN,
    WARNING_HEALTH_MIN,
)

NORMAL_LOSS_MAX = 2.0
WARNING_LOSS_MAX = 50.0
NORMAL_RTT_MAX = 45.0  # raised for 12-switch topology (normal e2e RTT is 30-50ms)
WARNING_RTT_MAX = 80.0
INSTABILITY_MDEV_RATIO = 0.25
RTT_SPIKE_MULTIPLIER = 1.75  # vs median baseline


def _normalize_link(u: str, v: str) -> str:
    return f"{min(u, v)}-{max(u, v)}"


def _link_layer(link_id: str) -> str:
    switches = link_id.split("-")
    if all(s in {"s1", "s2", "s3"} for s in switches):
        return "core"
    if all(s.startswith("s") and int(s[1:]) <= 7 for s in switches):
        return "distribution"
    return "access"


def _classify_link_severity(loss: float, latency: float, mdev: float, status: str) -> str:
    if status in ("timeout", "error") or loss >= WARNING_LOSS_MAX:
        return "down"
    # Only classify as 'down' on very high loss — high RTT alone is NOT a link failure
    if loss >= WARNING_LOSS_MAX * 0.5:  # 25% loss
        return "down"
    if loss >= NORMAL_LOSS_MAX or latency >= NORMAL_RTT_MAX or (latency > 0 and mdev / max(latency, 1.0) >= INSTABILITY_MDEV_RATIO):
        return "warning"
    return "up"


def _severity_rank(severity: str) -> int:
    return {"up": 0, "warning": 1, "down": 2, "recovery": 1}.get(severity, 0)


def analyze_link_states(
    latest_df: pd.DataFrame,
    topo,
    baseline_rtt: float,
) -> Tuple[Dict[str, str], Dict[str, Dict[str, float]], List[Dict[str, Any]]]:
    """Map host-pair telemetry to switch-link health."""
    all_links = topo.get_all_links() if topo else []
    links_status = {lk: "up" for lk in all_links}
    link_metrics: Dict[str, Dict[str, float]] = {
        lk: {"loss": 0.0, "latency": 0.0, "mdev": 0.0, "votes": 0.0}
        for lk in all_links
    }
    active_issues: List[Dict[str, Any]] = []

    if latest_df.empty or topo is None:
        return links_status, link_metrics, active_issues

    # 1. Load active failed/rerouted links from recovery_metrics.json to exclude them from path calculations of successful probes
    failed_links = []
    try:
        import json
        from pathlib import Path
        metrics_file = Path(__file__).resolve().parent.parent / "results" / "recovery_metrics.json"
        if metrics_file.exists():
            with open(metrics_file, "r") as f:
                data = json.load(f)
            last = data.get("last_recovery") or {}
            if last.get("status") == "SUCCESS" and last.get("failed_link"):
                fl = last.get("failed_link")
                failed_links = [lk.strip() for lk in fl.split(",") if lk.strip()]
    except Exception:
        pass

    # 2. Identify all links that are proven to be UP (active tomography)
    proven_up_links = set()
    for _, row in latest_df.iterrows():
        loss = float(row.get("packet_loss_pct", 0.0) or 0.0)
        rtt = float(row.get("rtt_avg_ms", 0.0) or 0.0)
        status = str(row.get("status", "ok")).lower()
        if loss < 2.0 and rtt < NORMAL_RTT_MAX and status in ("ok", "partial_loss"):
            src = row.get("source", "")
            dst = row.get("destination", "")
            src_sw = topo.get_switch_for_host(src)
            dst_sw = topo.get_switch_for_host(dst)
            if src_sw and dst_sw:
                # Use current active path excluding failed links
                path = topo.shortest_path(src_sw, dst_sw, failed_links)
                for i in range(len(path) - 1):
                    proven_up_links.add(_normalize_link(path[i], path[i + 1]))

    # 3. Blame logic with active tomography filtering to avoid false positives
    for _, row in latest_df.iterrows():
        loss = float(row.get("packet_loss_pct", 0.0) or 0.0)
        rtt = float(row.get("rtt_avg_ms", 0.0) or 0.0)
        mdev = float(row.get("rtt_mdev_ms", 0.0) or 0.0)
        status = str(row.get("status", "ok")).lower()
        src = row.get("source", "")
        dst = row.get("destination", "")

        src_sw = topo.get_switch_for_host(src)
        dst_sw = topo.get_switch_for_host(dst)
        if not src_sw or not dst_sw:
            continue

        path = topo.shortest_path(src_sw, dst_sw, failed_links)
        if len(path) < 2:
            continue

        pair_severity = _classify_link_severity(loss, rtt, mdev, status)
        if pair_severity == "up" and baseline_rtt > 0 and rtt > max(baseline_rtt * RTT_SPIKE_MULTIPLIER, WARNING_RTT_MAX * 0.5):
            pair_severity = "warning"

        if pair_severity == "up":
            continue

        # Distribute fault indicators to links along the path
        for i in range(len(path) - 1):
            link_name = _normalize_link(path[i], path[i + 1])
            if link_name not in links_status:
                continue

            # Active Tomography Shield: Never mark a link as DOWN/WARNING if successful probes prove it is functioning!
            if link_name in proven_up_links:
                continue

            link_metrics[link_name]["loss"] = max(link_metrics[link_name]["loss"], loss)
            link_metrics[link_name]["latency"] = max(link_metrics[link_name]["latency"], rtt)
            link_metrics[link_name]["mdev"] = max(link_metrics[link_name]["mdev"], mdev)
            link_metrics[link_name]["votes"] += 1.0

            # Tomography filter: only allow verified failed links (from recovery_metrics.json)
            # to be marked as "down" when recovery is active. If recovery is active and a link
            # is NOT in failed_links, restrict its status to "warning".
            link_sev = pair_severity
            if failed_links and link_sev == "down" and link_name not in failed_links:
                link_sev = "warning"

            if _severity_rank(link_sev) > _severity_rank(links_status[link_name]):
                links_status[link_name] = link_sev

        if pair_severity != "up":
            issue_type = "link_down" if pair_severity == "down" else "degradation"
            if loss >= NORMAL_LOSS_MAX:
                msg = f"Packet loss {loss:.1f}% on path {src}→{dst} ({'→'.join(path)})"
            elif rtt >= NORMAL_RTT_MAX:
                msg = f"High latency {rtt:.1f}ms on path {src}→{dst} ({'→'.join(path)})"
            else:
                msg = f"Link instability on path {src}→{dst} (mdev={mdev:.1f}ms)"

            active_issues.append(
                {
                    "type": issue_type,
                    "severity": pair_severity,
                    "source": src,
                    "destination": dst,
                    "path": path,
                    "path_str": "→".join(path),
                    "link": _normalize_link(path[0], path[1]) if len(path) >= 2 else "",
                    "switch": src_sw,
                    "loss_pct": round(loss, 2),
                    "rtt_ms": round(rtt, 2),
                    "message": msg,
                }
            )

    return links_status, link_metrics, active_issues


def derive_severity(
    health_score: int,
    max_loss: float,
    avg_latency: float,
    links_status: Dict[str, str],
    active_failures: int,
    recovery_active: bool,
    baseline_rtt: float = 10.0,
) -> str:
    """Authoritative severity derived strictly from health score tiers and recovery state."""
    if recovery_active or health_score < WARNING_HEALTH_MIN:
        return "CRITICAL"
    if health_score < NORMAL_HEALTH_MIN:
        return "WARNING"
    return "NORMAL"


def build_fault_analysis(
    links_status: Dict[str, str],
    link_metrics: Dict[str, Dict[str, float]],
    active_issues: List[Dict[str, Any]],
    avg_latency: float,
    max_loss: float,
    health_score: int,
) -> Dict[str, Any]:
    """Build structured fault panels and root-cause text."""
    failed_links = []
    degraded_links = []
    congested_switches: Dict[str, int] = defaultdict(int)

    for link_id, status in links_status.items():
        metrics = link_metrics.get(link_id, {})
        loss = metrics.get("loss", 0.0)
        latency = metrics.get("latency", 0.0)
        layer = _link_layer(link_id)
        sw_a, sw_b = link_id.split("-", 1)

        entry = {
            "link": link_id,
            "layer": layer,
            "loss_pct": round(loss, 2),
            "latency_ms": round(latency, 2),
            "status": status,
        }

        if status == "down":
            if loss >= WARNING_LOSS_MAX or loss == 0:
                entry["message"] = f"Link {link_id} DOWN"
            else:
                entry["message"] = f"Link {link_id} DOWN — {loss:.1f}% packet loss"
            failed_links.append(entry)
            congested_switches[sw_a] += 2
            congested_switches[sw_b] += 2
        elif status == "warning":
            if latency >= NORMAL_RTT_MAX and loss >= NORMAL_LOSS_MAX:
                entry["message"] = f"Congestion on {link_id} — {latency:.1f}ms, {loss:.1f}% loss"
            elif latency >= NORMAL_RTT_MAX:
                entry["message"] = f"High latency on {link_id} — {latency:.1f}ms"
            elif loss > 0:
                entry["message"] = f"Packet loss detected on {link_id} — {loss:.1f}%"
            else:
                entry["message"] = f"Link instability on {link_id}"
            degraded_links.append(entry)
            congested_switches[sw_a] += 1
            congested_switches[sw_b] += 1

    unstable_switches = [
        {"switch": sw, "score": score}
        for sw, score in sorted(congested_switches.items(), key=lambda x: -x[1])
        if score > 0
    ]

    root_causes: List[str] = []
    if failed_links:
        root_causes.append(
            f"Critical failure on {failed_links[0]['link']} ({failed_links[0]['layer']} layer)"
        )
    for dl in degraded_links[:3]:
        root_causes.append(dl["message"])
    if not root_causes and max_loss >= NORMAL_LOSS_MAX:
        root_causes.append(f"Network-wide packet loss elevated to {max_loss:.1f}%")
    if not root_causes and avg_latency >= NORMAL_RTT_MAX:
        root_causes.append(f"Average RTT {avg_latency:.1f}ms exceeds {NORMAL_RTT_MAX:.0f}ms baseline")
    if not root_causes and health_score < NORMAL_HEALTH_MIN:
        root_causes.append(f"Health score {health_score}/100 indicates degraded network conditions")
    if not root_causes:
        root_causes.append("All monitored links operating within normal thresholds")

    affected_paths = []
    seen_paths = set()
    for issue in active_issues:
        path_str = issue.get("path_str", "")
        if path_str and path_str not in seen_paths:
            seen_paths.add(path_str)
            affected_paths.append(
                {
                    "path": path_str,
                    "severity": issue.get("severity", "warning"),
                    "message": issue.get("message", ""),
                }
            )

    return {
        "failed_links": failed_links,
        "degraded_links": degraded_links,
        "unstable_switches": unstable_switches,
        "affected_paths": affected_paths[:5],
        "root_causes": root_causes[:5],
        "active_issues": active_issues[:8],
    }


def build_explanation(
    severity: str,
    fault_analysis: Dict[str, Any],
    avg_latency: float,
    max_loss: float,
    health_score: int,
) -> str:
    """Single-line contextual explanation for the KPI card."""
    if severity == "NORMAL":
        return f"Network healthy — {health_score}/100, {max_loss:.1f}% loss, {avg_latency:.1f}ms avg RTT"

    root = fault_analysis.get("root_causes") or []
    if root and root[0] != "All monitored links operating within normal thresholds":
        return root[0]

    if severity == "CRITICAL":
        return f"Critical fault — health {health_score}/100, {max_loss:.1f}% loss"
    return f"Degraded conditions — health {health_score}/100, {avg_latency:.1f}ms avg RTT"


def resolve_recovery_status(
    severity: str,
    links_status: Dict[str, str],
    recovery_data: Optional[Dict[str, Any]],
    recovery_path_links: List[str],
) -> Tuple[str, Dict[str, str]]:
    """Recovery display must match live network condition."""
    updated_links = dict(links_status)
    for link_id in recovery_path_links:
        if link_id in updated_links and updated_links[link_id] == "up":
            updated_links[link_id] = "recovery"

    has_down = any(s == "down" for s in links_status.values())
    has_warning = any(s == "warning" for s in links_status.values())
    recovery_active = bool(recovery_data and recovery_data.get("recovery_active"))

    if severity == "CRITICAL" or recovery_active:
        return "RECOVERING", updated_links

    last = (recovery_data or {}).get("last_recovery") or {}
    is_success = last.get("status") == "SUCCESS" or "selected_path" in last or "route" in last
    if last and is_success and severity == "NORMAL" and not has_down and not has_warning:
        path_name = last.get("selected_path", "alternate path")
        route = last.get("route") or last.get("recovery_path")
        if route:
            return f"RECOVERED ({path_name}: {route})", updated_links
        return f"RECOVERED ({path_name})", updated_links

    if severity == "WARNING" or has_warning:
        return "DEGRADED", updated_links

    return "NORMAL", updated_links


def extract_recovery_path_links(recovery_data: Optional[Dict[str, Any]]) -> List[str]:
    if not recovery_data:
        return []
    last = recovery_data.get("last_recovery") or {}
    path = last.get("recovery_path") or last.get("route") or []
    if isinstance(path, str):
        switches = [
            s.strip()
            for s in path.replace("→", "->").replace("—", "->").split("->")
            if s.strip()
        ]
    else:
        switches = list(path)
    links = []
    for i in range(len(switches) - 1):
        links.append(_normalize_link(switches[i], switches[i + 1]))
    return links


def analyze_network_state(
    latest_df: pd.DataFrame,
    topo,
    ml_predictions: Optional[List[Dict[str, Any]]] = None,
    recovery_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Full network analysis for the dashboard API.
    Metrics and topology are authoritative; ML augments confidence only.
    """
    valid_rtt = latest_df[latest_df["rtt_avg_ms"] > 0]["rtt_avg_ms"]
    avg_latency = float(valid_rtt.mean()) if not valid_rtt.empty else 0.0
    max_loss = float(latest_df["packet_loss_pct"].max()) if not latest_df.empty else 0.0
    active_failures = int(latest_df["status"].isin(["timeout", "error"]).sum()) if not latest_df.empty else 0
    baseline_rtt = float(valid_rtt.median()) if not valid_rtt.empty else 5.0

    links_status, link_metrics, active_issues = analyze_link_states(latest_df, topo, baseline_rtt)

    instability_count = int(
        (latest_df["rtt_mdev_ms"] / latest_df["rtt_avg_ms"].clip(lower=1.0) > INSTABILITY_MDEV_RATIO).sum()
    ) if not latest_df.empty else 0

    health_score = calculate_health_score(
        avg_latency=avg_latency,
        max_loss=max_loss,
        active_failures=active_failures,
        instability_count=instability_count,
    )

    recovery_path_links = extract_recovery_path_links(recovery_data)
    recovery_active = bool(recovery_data and recovery_data.get("recovery_active"))

    if recovery_active:
        health_score = min(health_score, 59)

    severity = derive_severity(
        health_score=health_score,
        max_loss=max_loss,
        avg_latency=avg_latency,
        links_status=links_status,
        active_failures=active_failures,
        recovery_active=recovery_active,
        baseline_rtt=baseline_rtt,
    )

    rtt_warning_threshold = max(WARNING_RTT_MAX * 0.5, baseline_rtt * RTT_SPIKE_MULTIPLIER)

    # Reconcile: never show WARNING/CRITICAL when metrics clearly say NORMAL
    if (
        health_score >= NORMAL_HEALTH_MIN
        and max_loss < NORMAL_LOSS_MAX
        and avg_latency < rtt_warning_threshold
        and not any(s != "up" for s in links_status.values())
        and active_failures == 0
        and not recovery_active
    ):
        severity = "NORMAL"

    fault_analysis = build_fault_analysis(
        links_status, link_metrics, active_issues, avg_latency, max_loss, health_score
    )

    recovery_status, links_status = resolve_recovery_status(
        severity, links_status, recovery_data, recovery_path_links
    )

    # Confidence from ML when available, otherwise metrics-based
    confidence = 100.0
    if ml_predictions:
        matching = [p for p in ml_predictions if p.get("severity") == severity]
        pool = matching or ml_predictions
        confidence = max(p.get("confidence", 0.0) for p in pool)
    else:
        if severity == "CRITICAL":
            confidence = min(99.0, 80.0 + max_loss * 0.2)
        elif severity == "WARNING":
            confidence = min(95.0, 70.0 + (NORMAL_HEALTH_MIN - health_score) * 0.5)

    explanation = build_explanation(severity, fault_analysis, avg_latency, max_loss, health_score)

    return {
        "ai_status": severity,
        "health_score": health_score,
        "health_label": get_health_label(health_score),
        "confidence": round(confidence, 1),
        "explanation": explanation,
        "packet_loss_pct": round(max_loss, 2),
        "rtt_avg_ms": round(avg_latency, 2),
        "recovery_status": recovery_status,
        "links": links_status,
        "link_metrics": {
            k: {
                "loss_pct": round(v["loss"], 2),
                "latency_ms": round(v["latency"], 2),
                "status": links_status.get(k, "up"),
            }
            for k, v in link_metrics.items()
            if links_status.get(k, "up") != "up"
        },
        "fault_analysis": fault_analysis,
        "recovery_path_links": recovery_path_links,
    }
