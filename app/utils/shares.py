"""
Whole-share policy for VestX.

SpaceX-style private equity planning assumes **integer shares only** —
no fractional lots for sell / exercise / planning qty.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Union

Number = Union[int, float]


def whole_shares(x: Optional[Any]) -> int:
    """
    Convert a quantity to a non-negative whole share count.

    Uses floor so we never plan more than inventory allows.
    Tiny epsilon avoids float noise (e.g. 10.999999999 → 11).
    """
    if x is None:
        return 0
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v) or v <= 0:
        return 0
    # If already essentially an integer, keep it
    nearest = round(v)
    if abs(v - nearest) < 1e-6:
        return max(0, int(nearest))
    return max(0, int(math.floor(v + 1e-9)))


def whole_shares_ceil(x: Optional[Any]) -> int:
    """
    Minimum whole shares to meet a continuous requirement (e.g. sell-to-cover).
    """
    if x is None:
        return 0
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v) or v <= 0:
        return 0
    nearest = round(v)
    if abs(v - nearest) < 1e-6:
        return max(0, int(nearest))
    return max(0, int(math.ceil(v - 1e-9)))


def clamp_whole_shares(qty: Optional[Any], maximum: Optional[Any]) -> int:
    """Whole shares not exceeding maximum available."""
    q = whole_shares(qty)
    m = whole_shares(maximum) if maximum is not None else q
    return min(q, m)
