"""Deterministic scoring helpers for certification and readout."""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp, sqrt
from typing import Any


def success_mean(alpha_success: float, beta_failure: float) -> float:
    total = alpha_success + beta_failure
    if total <= 0:
        return 0.0
    return alpha_success / total


def success_lcb(alpha_success: float, beta_failure: float, z: float = 1.96) -> float:
    """Normal-approximate lower confidence bound for a beta posterior mean."""
    total = alpha_success + beta_failure
    if total <= 0:
        return 0.0
    mean = success_mean(alpha_success, beta_failure)
    se = sqrt(max(mean * (1.0 - mean), 0.0) / total)
    return max(0.0, mean - z * se)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def freshness(last_verified: str | None, now: str | None = None, decay_seconds: float = 86_400.0) -> float:
    last_dt = parse_time(last_verified)
    if last_dt is None:
        return 0.0
    now_dt = parse_time(now) or datetime.now(timezone.utc)
    age = max((now_dt - last_dt).total_seconds(), 0.0)
    return exp(-age / max(decay_seconds, 1.0))


def trust_score(posterior: dict[str, Any], now: str | None = None) -> float:
    alpha = float(posterior.get("alpha_success", 1.0))
    beta = float(posterior.get("beta_failure", 1.0))
    lcb = success_lcb(alpha, beta)
    contradiction = float(posterior.get("contradiction_count", 0.0))
    calibration = float(posterior.get("calibration_error", 0.0))
    fresh = freshness(
        posterior.get("last_verified"),
        now=now,
        decay_seconds=float(posterior.get("freshness_decay", 86_400.0)),
    )
    penalty = exp(-0.5 * contradiction - calibration)
    return max(0.0, min(1.0, lcb * fresh * penalty))


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return sum((value - m) ** 2 for value in values) / (len(values) - 1)

