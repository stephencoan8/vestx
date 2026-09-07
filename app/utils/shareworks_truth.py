"""
Shareworks statement snapshot used as lot-count ground truth.

Live grant/vest rows still live in the DB. This module is the check
against the Aug 2026 statement so a 7–11 share drift is visible instead
of silently shifting planning cash by a few thousand dollars.

Run scripts/_diag_portfolio_sw.py against prod to refresh the deltas.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from app.utils.shares import whole_shares

# Statement buckets (shares)
SW_AVAILABLE_COMMON = 4005
SW_AVAILABLE_COMMON_NT = 2500
SW_AVAILABLE_ESPP = 4400
SW_AVAILABLE_ISO = 1055  # unexercised options
SW_UNAVAILABLE_RSU = 8371
SW_UNAVAILABLE_ISO = 4565
SW_NEXT_RSU_VEST = 1590  # combined Nov cycle (Shareworks)

# Last known VestX deltas from the Aug 29–Sep 7 reviews (planning, not SSOT)
# ISO available 1,065 vs 1,055; next RSU vest 1,579 vs 1,590; available ~7 sh.
REVIEW_DRIFT = {
    'available_iso': 10,  # VX − SW
    'next_rsu_vest': -11,
    'available_stock': 7,
}


def sw_available_stock() -> int:
    return SW_AVAILABLE_COMMON + SW_AVAILABLE_COMMON_NT + SW_AVAILABLE_ESPP


def drift_vs_shareworks(
    vx: Mapping[str, Any],
    *,
    next_rsu_vest: Optional[float] = None,
) -> Dict[str, int]:
    """Integer share deltas (VestX − Shareworks). 0 is a match."""
    iso = whole_shares(vx.get('iso_unexercised') or vx.get('available_iso'))
    avail = whole_shares(
        vx.get('held_shares')
        or vx.get('shares_available')
        or vx.get('available_stock')
    )
    un_rsu = whole_shares(vx.get('unavailable_shares_rsu') or vx.get('unavailable_rsu'))
    un_iso = whole_shares(vx.get('unavailable_shares_iso') or vx.get('unavailable_iso'))
    next_v = whole_shares(next_rsu_vest) if next_rsu_vest is not None else None
    out = {
        'available_iso': iso - SW_AVAILABLE_ISO,
        'available_stock': avail - sw_available_stock() if avail else 0,
        'unavailable_rsu': un_rsu - SW_UNAVAILABLE_RSU if un_rsu else 0,
        'unavailable_iso': un_iso - SW_UNAVAILABLE_ISO if un_iso else 0,
    }
    if next_v is not None:
        out['next_rsu_vest'] = next_v - SW_NEXT_RSU_VEST
    return out
