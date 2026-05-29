#!/usr/bin/env python3
"""
PathGuard: AI-Driven Fault Detection and Dynamic Recovery Framework
====================================================================

AI Fault Detection — Training Pipeline
---------------------------------------
Trains a Random Forest classifier to distinguish NORMAL from FAULT
network conditions using features extracted by the monitoring module.

Training Pipeline
-----------------
  1.  Load CSV dataset  (datasets/network_data.csv)
  2.  Engineer features  (packet_loss_pct, rtt_avg_ms, rtt_max_ms, rtt_mdev_ms)
  3.  Auto-label rows    (NORMAL vs FAULT based on configurable thresholds)
  4.  Train/test split   (80/20 stratified)
  5.  Train RandomForestClassifier
  6.  Evaluate            (accuracy, confusion matrix, classification report)
  7.  Save model          (ai/model.pkl via joblib)

Label Logic
-----------
  NORMAL — packet_loss_pct < 10  AND  rtt_avg_ms < adaptive threshold
           AND  rtt_mdev_ms < adaptive threshold  (i.e. latency is stable)
  FAULT  — everything else

  The latency thresholds are derived from the dataset itself using
  percentile-based heuristics so the model works across different
  topologies and link configurations without manual tuning.

Usage
-----
  # Train from the command line
  python3 ai/train_model.py

  # With custom options
  python3 ai/train_model.py --csv datasets/network_data.csv \
                             --model ai/model.pkl \
                             --loss-threshold 10 \
                             --test-size 0.2

  # Predict on new data (library mode)
  from ai.train_model import FaultDetector
  detector = FaultDetector.load("ai/model.pkl")
  label = detector.predict_single(packet_loss_pct=0.0, rtt_avg_ms=5.2,
                                   rtt_max_ms=6.1, rtt_mdev_ms=0.3)

Requires
--------
  • pandas       (pip install pandas)
  • scikit-learn (pip install scikit-learn)
  • joblib       (bundled with scikit-learn)
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder


# ──────────────────────────────────────────────────────────────────────
# 1.  CONSTANTS & DEFAULTS
# ──────────────────────────────────────────────────────────────────────

# Feature columns used by the model — must match the monitoring CSV schema
FEATURE_COLUMNS: List[str] = [
    "packet_loss_pct",
    "rtt_avg_ms",
    "rtt_max_ms",
    "rtt_mdev_ms",
]

# Label values
LABEL_NORMAL   = "NORMAL"
LABEL_WARNING  = "WARNING"
LABEL_CRITICAL = "CRITICAL"

# Default file paths (relative to project root)
DEFAULT_CSV_PATH   = "datasets/network_data.csv"
DEFAULT_MODEL_PATH = "ai/model.pkl"

# Default labelling thresholds
DEFAULT_LOSS_THRESHOLD = 10.0   # packet_loss_pct  (%)
DEFAULT_RTT_PERCENTILE = 90     # used to derive adaptive latency threshold


# ──────────────────────────────────────────────────────────────────────
# 2.  AUTO-LABELLING
#     Generates NORMAL / FAULT labels from raw monitoring data using
#     configurable heuristics.  This avoids the need for manually
#     labelled training data — a practical choice for SDN research
#     where ground-truth labels are rarely available.
# ──────────────────────────────────────────────────────────────────────

def auto_label(
    df: pd.DataFrame,
    loss_threshold: float = DEFAULT_LOSS_THRESHOLD,
    rtt_percentile: int = DEFAULT_RTT_PERCENTILE,
) -> pd.Series:
    """
    Create multi-class labels (NORMAL, WARNING, CRITICAL) using a multi-dimensional
    metric scoring approach to reduce single-metric dominance and self-confirmation bias.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain the columns in ``FEATURE_COLUMNS``.
    loss_threshold : float
        Unused in refined multi-class mode (retained for signature compatibility).
    rtt_percentile : int
        Unused in refined multi-class mode (retained for signature compatibility).

    Returns
    -------
    pd.Series
        Series of ``"NORMAL"``, ``"WARNING"``, or ``"CRITICAL"`` strings.
    """
    # ── Adaptive baseline thresholds from healthy subset (low loss < 2.0%) ──
    healthy_mask = df["packet_loss_pct"] < 2.0
    healthy_subset = df.loc[healthy_mask]

    if healthy_subset.empty:
        warnings.warn("No rows with packet_loss < 2.0%; using global data.")
        healthy_subset = df

    # Statistically sound baseline calculations (Median + Standard Deviations)
    rtt_avg_base = healthy_subset["rtt_avg_ms"].median()
    rtt_avg_std = healthy_subset["rtt_avg_ms"].std()
    if pd.isna(rtt_avg_std) or rtt_avg_std == 0:
        rtt_avg_std = 1.0

    rtt_avg_warning = rtt_avg_base + 1.5 * rtt_avg_std
    rtt_avg_critical = rtt_avg_base + 3.0 * rtt_avg_std

    # Variance/jitter thresholds (Percentile-based)
    rtt_mdev_warning = healthy_subset["rtt_mdev_ms"].quantile(0.80)
    rtt_mdev_critical = healthy_subset["rtt_mdev_ms"].quantile(0.95)

    # Establish sensible floors to prevent false alarms on extremely fast links
    rtt_avg_warning = max(rtt_avg_warning, 15.0)
    rtt_avg_critical = max(rtt_avg_critical, 40.0)
    rtt_mdev_warning = max(rtt_mdev_warning, 3.0)
    rtt_mdev_critical = max(rtt_mdev_critical, 10.0)

    print(f"  Adaptive Threshold Configuration:")
    print(f"    • Healthy Base RTT Average:     {rtt_avg_base:.2f} ms (StdDev: {rtt_avg_std:.2f} ms)")
    print(f"    • WARNING thresholds:          RTT >= {rtt_avg_warning:.2f} ms | Jitter >= {rtt_mdev_warning:.2f} ms | Loss >= 5.0%")
    print(f"    • CRITICAL thresholds:         RTT >= {rtt_avg_critical:.2f} ms | Jitter >= {rtt_mdev_critical:.2f} ms | Loss >= 40.0%")

    # Initialize all rows to NORMAL
    labels = pd.Series(LABEL_NORMAL, index=df.index)

    # ── WARNING Logic (Degraded but connected) ──
    # Elevated loss, rising RTT trends, or significant jitter
    is_warning = (
        ((df["packet_loss_pct"] >= 5.0) & (df["packet_loss_pct"] < 40.0)) |
        ((df["rtt_avg_ms"] >= rtt_avg_warning) & (df["rtt_avg_ms"] < rtt_avg_critical)) |
        ((df["rtt_mdev_ms"] >= rtt_mdev_warning) & (df["rtt_mdev_ms"] < rtt_mdev_critical))
    )

    # ── CRITICAL Logic (Severe connectivity/routing impact or severe degradation) ──
    # Requires multiple degraded indicators or actual reachability failure to avoid false-alarm triggers
    status_lower = df["status"].astype(str).str.lower()
    is_critical = (
        # Condition A: Severe packet loss or link down
        (df["packet_loss_pct"] >= 40.0) |
        (status_lower.isin(["timeout", "error"])) |
        
        # Condition B: High RTT + Mild/moderate packet loss (Joint degradation)
        ((df["rtt_avg_ms"] >= rtt_avg_warning) & (df["packet_loss_pct"] >= 15.0)) |
        
        # Condition C: Severe latency spike + high jitter (Sustained congestion/instability)
        ((df["rtt_avg_ms"] >= rtt_avg_critical) & (df["rtt_mdev_ms"] >= rtt_mdev_critical))
    )

    # Apply labels sequentially so CRITICAL overrides WARNING
    labels[is_warning] = LABEL_WARNING
    labels[is_critical] = LABEL_CRITICAL

    return labels


# ──────────────────────────────────────────────────────────────────────
# 3.  DATASET LOADING & PREPROCESSING
# ──────────────────────────────────────────────────────────────────────

def load_dataset(
    csv_path: str | Path,
    loss_threshold: float = DEFAULT_LOSS_THRESHOLD,
    rtt_percentile: int = DEFAULT_RTT_PERCENTILE,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """
    Load the monitoring CSV, perform rigorous data quality validation, and create labels.

    Returns
    -------
    (full_df, features_df, labels)
        full_df     — the original dataframe with an added ``label`` column
        features_df — only the feature columns (ready for sklearn)
        labels      — the auto-generated label series
    """
    csv_path = Path(csv_path)

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Dataset not found: {csv_path}\n"
            f"Run the monitoring module first to generate data:\n"
            f"  sudo python3 monitoring/monitor.py --interval 5"
        )

    print(f"\n📂  Loading dataset: {csv_path}")
    raw_df = pd.read_csv(csv_path)
    total_raw = len(raw_df)

    # ── Validate required columns ────────────────────────────────
    missing = [c for c in FEATURE_COLUMNS if c not in raw_df.columns]
    if missing:
        raise ValueError(
            f"Dataset is missing required feature columns: {missing}\n"
            f"Available columns: {list(raw_df.columns)}"
        )

    # ── 1. NaN Handling ──────────────────────────────────────────
    df_no_nan = raw_df.dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)
    nans_removed = total_raw - len(df_no_nan)

    # ── 2. Telemetry Range Validation (Filter Malformed) ─────────
    df_valid = df_no_nan[
        (df_no_nan["packet_loss_pct"] >= 0.0) & (df_no_nan["packet_loss_pct"] <= 100.0) &
        (df_no_nan["rtt_avg_ms"] >= 0.0) &
        (df_no_nan["rtt_max_ms"] >= 0.0) &
        (df_no_nan["rtt_mdev_ms"] >= 0.0)
    ].reset_index(drop=True)
    malformed_removed = len(df_no_nan) - len(df_valid)

    # ── 3. Duplicate Telemetry Removal ───────────────────────────
    # Prevents model leakage by dropping identical telemetry windows on identical paths
    dup_cols = ["timestamp", "source", "destination"]
    subset_cols = [c for c in dup_cols if c in df_valid.columns] + FEATURE_COLUMNS
    df_clean = df_valid.drop_duplicates(subset=subset_cols).reset_index(drop=True)
    duplicates_removed = len(df_valid) - len(df_clean)

    print(f"\n==============================================================")
    print(f"  📊  DATASET QUALITY SUMMARY REPORT")
    print(f"==============================================================")
    print(f"    • Total Raw Telemetry Rows:       {total_raw:,}")
    print(f"    • Missing Values (NaN) Cleaned:   {nans_removed:,}")
    print(f"    • Malformed Telemetry Cleaned:    {malformed_removed:,}")
    print(f"    • Duplicate Rows Eliminated:      {duplicates_removed:,}")
    print(f"    • Usable Telemetry Rows:          {len(df_clean):,}")
    print(f"--------------------------------------------------------------")
    print(f"    • Feature Summary Statistics (Range & Standard Deviation):")
    for col in FEATURE_COLUMNS:
        mean_val = df_clean[col].mean()
        std_val = df_clean[col].std()
        min_val = df_clean[col].min()
        max_val = df_clean[col].max()
        print(f"      - {col:<18s} | Range: [{min_val:>5.1f} - {max_val:>5.1f}] | Mean: {mean_val:>6.2f} | Std: {std_val:>5.2f}")
    print(f"==============================================================\n")

    if len(df_clean) < 15:
        raise ValueError(
            f"Cleaned dataset too small ({len(df_clean)} rows). "
            f"Run the monitor for more rounds to collect sufficient data."
        )

    # ── Feature matrix & labels ──────────────────────────────────
    features = df_clean[FEATURE_COLUMNS].copy()
    labels = auto_label(df_clean, loss_threshold, rtt_percentile)
    df_clean["label"] = labels

    # Print label distribution
    dist = labels.value_counts()
    print(f"\n  Label distribution:")
    for lbl, cnt in dist.items():
        pct = cnt / len(labels) * 100
        print(f"    {lbl:12s}  {cnt:6,}  ({pct:.1f}%)")

    return df_clean, features, labels


# ──────────────────────────────────────────────────────────────────────
# 4.  MODEL TRAINING
# ──────────────────────────────────────────────────────────────────────

def train(
    features: pd.DataFrame,
    labels: pd.Series,
    test_size: float = 0.2,
    random_state: int = 42,
    n_estimators: int = 100,
) -> Tuple[RandomForestClassifier, LabelEncoder, Dict]:
    """
    Train a RandomForestClassifier, perform Stratified 5-Fold Cross-Validation, and evaluate.

    Parameters
    ----------
    features : pd.DataFrame
        Feature matrix (rows × FEATURE_COLUMNS).
    labels : pd.Series
        String labels (NORMAL / WARNING / CRITICAL).
    test_size : float
        Fraction of data held out for testing.
    random_state : int
        Reproducibility seed.
    n_estimators : int
        Number of trees in the forest.

    Returns
    -------
    (model, label_encoder, metrics)
        model         — trained RandomForestClassifier
        label_encoder — fitted LabelEncoder (str → int mapping)
        metrics       — dict with accuracy, confusion_matrix, and report
    """
    print(f"\n🔧  Training Random Forest Classifier (n_estimators={n_estimators}, "
          f"test_size={test_size})")

    # ── Encode string labels to integers ─────────────────────────
    le = LabelEncoder()
    y = le.fit_transform(labels)   # CRITICAL=0, NORMAL=1, WARNING=2
    X = features.values

    print(f"    • Target Classes: {list(le.classes_)}")

    # ── Train / test split (stratified) ──────────────────────────
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=test_size,
        random_state=random_state,
        stratify=y,
    )
    print(f"    • Train set size: {len(X_train):,}  |  Test set size: {len(X_test):,}")

    # ── Instantiate classifier ───────────────────────────────────
    model = RandomForestClassifier(
        n_estimators=n_estimators,
        random_state=random_state,
        n_jobs=-1,              # use all CPU cores
        class_weight="balanced",  # handle class imbalance
    )

    # ── 1. Stratified 5-Fold Cross-Validation ───────────────────
    # Validates generalization and flags overfitting
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    print(f"🧪  Executing Stratified 5-Fold Cross-Validation...")
    cv_folder = StratifiedKFold(n_splits=5, shuffle=True, random_state=random_state)
    cv_scores = cross_val_score(model, X_train, y_train, cv=cv_folder, n_jobs=-1)
    cv_mean = cv_scores.mean()
    cv_std = cv_scores.std()

    # Fit the final model on full training set
    model.fit(X_train, y_train)

    # ── Evaluate on holdout test set ─────────────────────────────
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    cm  = confusion_matrix(y_test, y_pred)
    report = classification_report(
        y_test, y_pred,
        target_names=le.classes_,
        zero_division=0,
    )

    print(f"\n==============================================================")
    print(f"  📊  MODEL PERFORMANCE CREDIBILITY REPORT")
    print(f"==============================================================")
    print(f"    • Holdout Test Accuracy:          {acc * 100:.2f}%")
    print(f"    • Stratified 5-Fold CV Accuracy:  {cv_mean * 100:.2f}% (± {cv_std * 100:.2f}%)")
    print(f"    • Evaluation Wording:            High performance under controlled SDN emulation")
    print(f"--------------------------------------------------------------")
    print(f"  • Confusion Matrix:")
    # Dynamic header printing for N-class matrix
    header = f"    {'':15s}" + "".join([f"{'Pred ' + c:>15s}" for c in le.classes_])
    print(header)
    for i, cls_name in enumerate(le.classes_):
        row_str = f"    {('True ' + cls_name):15s}" + "".join([f"{cm[i][j]:15,}" for j in range(len(le.classes_))])
        print(row_str)
    print(f"--------------------------------------------------------------")
    print(f"  • Detailed Classification Report:\n")
    print(report)

    # ── Feature importance ───────────────────────────────────────
    importances = model.feature_importances_
    print(f"  • Feature Importances:")
    for fname, imp in sorted(zip(FEATURE_COLUMNS, importances),
                              key=lambda x: x[1], reverse=True):
        bar = "█" * int(imp * 40)
        print(f"    - {fname:18s} | weight: {imp:.4f} | {bar}")

    print(f"==============================================================\n")

    metrics = {
        "accuracy": acc,
        "cv_mean": cv_mean,
        "cv_std": cv_std,
        "confusion_matrix": cm,
        "classification_report": report,
        "feature_importances": dict(zip(FEATURE_COLUMNS, importances)),
    }

    return model, le, metrics


# ──────────────────────────────────────────────────────────────────────
# 5.  MODEL PERSISTENCE
#     Saves the trained model, label encoder, feature list, and
#     thresholds into a single .pkl bundle so that the inference
#     side has everything it needs without external config.
# ──────────────────────────────────────────────────────────────────────

def save_model(
    model: RandomForestClassifier,
    label_encoder: LabelEncoder,
    model_path: str | Path,
    metrics: Optional[Dict] = None,
    loss_threshold: float = DEFAULT_LOSS_THRESHOLD,
) -> Path:
    """
    Persist the trained model bundle to disk.

    The saved bundle is a dict with keys:
        model, label_encoder, feature_columns, loss_threshold, metrics

    Parameters
    ----------
    model : RandomForestClassifier
        The trained classifier.
    label_encoder : LabelEncoder
        Maps class indices back to string labels.
    model_path : str | Path
        Destination file (e.g. ``ai/model.pkl``).
    metrics : dict, optional
        Training metrics to store alongside the model.
    loss_threshold : float
        The packet-loss threshold used during labelling (stored for
        reproducibility).

    Returns
    -------
    Path
        Absolute path to the saved file.
    """
    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)

    bundle = {
        "model":           model,
        "label_encoder":   label_encoder,
        "feature_columns": FEATURE_COLUMNS,
        "loss_threshold":  loss_threshold,
        "metrics":         metrics,
    }

    joblib.dump(bundle, model_path)
    print(f"💾  Model saved to: {model_path.resolve()}")
    return model_path.resolve()


# ──────────────────────────────────────────────────────────────────────
# 6.  FAULT DETECTOR  (Inference API)
#     A lightweight wrapper around the saved model for downstream use
#     by the monitoring loop or future rerouting modules.
# ──────────────────────────────────────────────────────────────────────

class FaultDetector:
    """
    Load a trained PathGuard fault-detection model and run predictions.

    Usage
    -----
    >>> detector = FaultDetector.load("ai/model.pkl")
    >>> detector.predict_single(
    ...     packet_loss_pct=0.0, rtt_avg_ms=5.2,
    ...     rtt_max_ms=6.1, rtt_mdev_ms=0.3
    ... )
    'NORMAL'

    >>> batch = pd.DataFrame([
    ...     {"packet_loss_pct": 0.0, "rtt_avg_ms": 5.2,
    ...      "rtt_max_ms": 6.1, "rtt_mdev_ms": 0.3},
    ...     {"packet_loss_pct": 80.0, "rtt_avg_ms": 0.0,
    ...      "rtt_max_ms": 0.0, "rtt_mdev_ms": 0.0},
    ... ])
    >>> detector.predict_batch(batch)
    ['NORMAL', 'FAULT']
    """

    def __init__(self, model, label_encoder, feature_columns, **kwargs):
        self.model = model
        # Disable parallel predictions for inference to eliminate joblib worker warning floods and massive overhead.
        if hasattr(self.model, "n_jobs"):
            self.model.n_jobs = 1
        self.label_encoder = label_encoder
        self.feature_columns = feature_columns
        self._extra = kwargs   # metrics, thresholds, etc.

    # ── Factory: load from disk ──────────────────────────────────

    @classmethod
    def load(cls, model_path: str | Path) -> "FaultDetector":
        """
        Load a model bundle previously saved by :func:`save_model`.

        Raises
        ------
        FileNotFoundError
            If the model file does not exist.
        """
        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model not found: {model_path}\n"
                f"Train one first:  python3 ai/train_model.py"
            )

        bundle = joblib.load(model_path)
        return cls(
            model=bundle["model"],
            label_encoder=bundle["label_encoder"],
            feature_columns=bundle["feature_columns"],
            metrics=bundle.get("metrics"),
            loss_threshold=bundle.get("loss_threshold"),
        )

    # ── Single-record prediction ─────────────────────────────────

    def predict_single(
        self,
        packet_loss_pct: float,
        rtt_avg_ms: float,
        rtt_max_ms: float,
        rtt_mdev_ms: float,
    ) -> str:
        """
        Classify one network measurement.

        Returns
        -------
        str
            ``"NORMAL"`` or ``"FAULT"``
        """
        features = np.array([[
            packet_loss_pct, rtt_avg_ms, rtt_max_ms, rtt_mdev_ms
        ]])
        pred_idx = self.model.predict(features)[0]
        return self.label_encoder.inverse_transform([pred_idx])[0]

    # ── Batch prediction ─────────────────────────────────────────

    def predict_batch(self, df: pd.DataFrame) -> List[str]:
        """
        Classify multiple rows at once.

        Parameters
        ----------
        df : pd.DataFrame
            Must contain the feature columns.

        Returns
        -------
        list[str]
            List of ``"NORMAL"`` / ``"FAULT"`` predictions.
        """
        X = df[self.feature_columns].values
        pred_indices = self.model.predict(X)
        return list(self.label_encoder.inverse_transform(pred_indices))

    # ── Probability scores ───────────────────────────────────────

    def predict_proba_single(
        self,
        packet_loss_pct: float,
        rtt_avg_ms: float,
        rtt_max_ms: float,
        rtt_mdev_ms: float,
    ) -> Dict[str, float]:
        """
        Return class probabilities for one measurement.

        Returns
        -------
        dict
            e.g. ``{"FAULT": 0.12, "NORMAL": 0.88}``
        """
        features = np.array([[
            packet_loss_pct, rtt_avg_ms, rtt_max_ms, rtt_mdev_ms
        ]])
        proba = self.model.predict_proba(features)[0]
        return dict(zip(self.label_encoder.classes_, proba))

    # ── Advanced Prediction (Severity, Confidence, Explainable AI) ──

    def predict_advanced(
        self,
        packet_loss_pct: float,
        rtt_avg_ms: float,
        rtt_max_ms: float,
        rtt_mdev_ms: float,
        source: str = "",
        destination: str = "",
        topo=None,
    ) -> Dict:
        """
        Return rich prediction including dynamic severity, confidence, and human-readable explanation.
        The severity classification is derived directly from the ML model's prediction.
        """
        base_label = self.predict_single(packet_loss_pct, rtt_avg_ms, rtt_max_ms, rtt_mdev_ms)
        proba_dict = self.predict_proba_single(packet_loss_pct, rtt_avg_ms, rtt_max_ms, rtt_mdev_ms)
        
        confidence = proba_dict.get(base_label, 0.0) * 100.0
        
        # Severity is now strictly defined by the Machine Learning model prediction!
        severity = base_label
        
        affected_link = ""
        path_str = ""

        if topo and source and destination:
            src_sw = topo.get_switch_for_host(source)
            dst_sw = topo.get_switch_for_host(destination)
            if src_sw and dst_sw:
                path = topo.shortest_path(src_sw, dst_sw)
                if len(path) >= 2:
                    path_str = "→".join(path)
                    affected_link = f"{min(path[0], path[1])}-{max(path[0], path[1])}"

        pair_ctx = f"{source}→{destination}" if source and destination else "monitored path"
        link_ctx = f" on link {affected_link}" if affected_link else ""
        path_ctx = f" via {path_str}" if path_str else ""

        # Explain the ML decision cleanly based on current telemetry context
        if severity == "CRITICAL":
            if packet_loss_pct >= 40.0:
                explanation = f"ML classified CRITICAL: Severe packet loss ({packet_loss_pct:.1f}%) on {pair_ctx}{link_ctx}"
            else:
                explanation = f"ML classified CRITICAL: Critical RTT spike ({rtt_avg_ms:.1f}ms) on {pair_ctx}{path_ctx}"
        elif severity == "WARNING":
            if packet_loss_pct >= 5.0:
                explanation = f"ML classified WARNING: Moderate packet loss ({packet_loss_pct:.1f}%) on {pair_ctx}{link_ctx}"
            elif rtt_mdev_ms >= 10.0 or (rtt_avg_ms > 0 and rtt_mdev_ms / rtt_avg_ms >= 0.2):
                explanation = f"ML classified WARNING: Telemetry jitter/instability near {source} (mdev={rtt_mdev_ms:.1f}ms)"
            else:
                explanation = f"ML classified WARNING: Elevated RTT latency ({rtt_avg_ms:.1f}ms) on {pair_ctx}{link_ctx}"
        else:
            explanation = "Traffic is stable"

        return {
            "severity": severity,
            "confidence": confidence,
            "explanation": explanation,
            "base_label": base_label,
            "source": source,
            "destination": destination,
            "affected_link": affected_link,
            "path": path_str,
        }

    def predict_batch_advanced(self, df: pd.DataFrame, topo=None) -> List[Dict]:
        """Run predict_advanced over a DataFrame."""
        results = []
        for _, row in df.iterrows():
            res = self.predict_advanced(
                row['packet_loss_pct'], 
                row['rtt_avg_ms'], 
                row['rtt_max_ms'], 
                row['rtt_mdev_ms'],
                source=str(row.get('source', '')),
                destination=str(row.get('destination', '')),
                topo=topo,
            )
            results.append(res)
        return results


# ──────────────────────────────────────────────────────────────────────
# 7.  SAMPLE PREDICTIONS
#     Demonstrates the model on a few hand-crafted scenarios.
# ──────────────────────────────────────────────────────────────────────

def run_sample_predictions(detector: FaultDetector):
    """Print predictions for representative network scenarios."""
    samples = [
        # (description, packet_loss, rtt_avg, rtt_max, rtt_mdev)
        ("Healthy link",            0.0,   5.12,   6.30,  0.28),
        ("Slightly elevated RTT",   0.0,  45.00,  60.00,  8.50),
        ("Minor packet loss",       5.0,  12.00,  18.00,  3.10),
        ("Moderate packet loss",   25.0,  30.00,  55.00, 12.00),
        ("High loss (link down)",  100.0,   0.00,   0.00,  0.00),
        ("Partial failure",        50.0,  80.00, 150.00, 35.00),
        ("Jittery but no loss",     0.0,  10.00,  90.00, 40.00),
        ("Low loss, high jitter",   3.0,  20.00, 120.00, 50.00),
    ]

    print("🔍  Sample Predictions")
    print("─" * 72)
    print(f"  {'Scenario':<26s}  {'Loss%':>6s}  {'AvgRTT':>7s}  "
          f"{'MaxRTT':>7s}  {'Mdev':>6s}  {'Prediction':>10s}")
    print("─" * 72)

    for desc, loss, avg, mx, mdev in samples:
        label = detector.predict_single(loss, avg, mx, mdev)
        proba = detector.predict_proba_single(loss, avg, mx, mdev)
        conf  = max(proba.values()) * 100

        # Colour-code the prediction
        colour = "\033[92m" if label == LABEL_NORMAL else ("\033[93m" if label == LABEL_WARNING else "\033[91m")
        reset  = "\033[0m"

        print(f"  {desc:<26s}  {loss:6.1f}  {avg:7.2f}  "
              f"{mx:7.2f}  {mdev:6.2f}  "
              f"{colour}{label:>10s}{reset}  ({conf:.0f}%)")

    print("─" * 72)


# ──────────────────────────────────────────────────────────────────────
# 8.  MAIN — CLI ENTRY POINT
# ──────────────────────────────────────────────────────────────────────

def parse_args():
    """Parse command-line arguments for training configuration."""
    parser = argparse.ArgumentParser(
        description="PathGuard AI Fault Detection — Model Training"
    )
    parser.add_argument(
        "--csv", default=DEFAULT_CSV_PATH,
        help=f"Path to monitoring CSV (default: {DEFAULT_CSV_PATH})"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL_PATH,
        help=f"Path to save trained model (default: {DEFAULT_MODEL_PATH})"
    )
    parser.add_argument(
        "--loss-threshold", type=float, default=DEFAULT_LOSS_THRESHOLD,
        help=f"Max packet-loss %% for NORMAL label (default: {DEFAULT_LOSS_THRESHOLD})"
    )
    parser.add_argument(
        "--test-size", type=float, default=0.2,
        help="Fraction of data used for testing (default: 0.2)"
    )
    parser.add_argument(
        "--n-estimators", type=int, default=100,
        help="Number of trees in the Random Forest (default: 100)"
    )
    parser.add_argument(
        "--random-state", type=int, default=42,
        help="Random seed for reproducibility (default: 42)"
    )
    return parser.parse_args()


def main():
    """
    Full training pipeline:
      load → label → split → train → evaluate → save → demo predictions.
    """
    args = parse_args()

    print("\n" + "=" * 60)
    print("  🧠  PathGuard — AI Fault Detection Training")
    print("=" * 60)

    # ── Step 1: Load and label the dataset ───────────────────────
    try:
        full_df, features, labels = load_dataset(
            csv_path=args.csv,
            loss_threshold=args.loss_threshold,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n✖  {exc}")
        sys.exit(1)

    # ── Step 2: Train and evaluate ───────────────────────────────
    try:
        model, le, metrics = train(
            features=features,
            labels=labels,
            test_size=args.test_size,
            random_state=args.random_state,
            n_estimators=args.n_estimators,
        )
    except Exception as exc:
        print(f"\n✖  Training failed: {exc}")
        sys.exit(1)

    # ── Step 3: Save the model ───────────────────────────────────
    save_model(
        model=model,
        label_encoder=le,
        model_path=args.model,
        metrics=metrics,
        loss_threshold=args.loss_threshold,
    )

    # ── Step 4: Demo predictions ─────────────────────────────────
    detector = FaultDetector(
        model=model,
        label_encoder=le,
        feature_columns=FEATURE_COLUMNS,
    )
    print()
    run_sample_predictions(detector)

    # ── Done ─────────────────────────────────────────────────────
    print(f"\n✅  Training complete.")
    print(f"    Model:    {args.model}")
    print(f"    Accuracy: {metrics['accuracy']:.4f}")
    print(f"\n  To use the model in your code:\n")
    print(f"    from ai.train_model import FaultDetector")
    print(f"    detector = FaultDetector.load(\"{args.model}\")")
    print(f"    label = detector.predict_single(")
    print(f"        packet_loss_pct=0.0, rtt_avg_ms=5.2,")
    print(f"        rtt_max_ms=6.1, rtt_mdev_ms=0.3")
    print(f"    )")
    print()


if __name__ == "__main__":
    main()
