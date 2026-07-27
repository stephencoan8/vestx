"""
One-time stock-split restatement helpers.

Equity share counts × ratio; per-share prices ÷ ratio.
Cash grants (share_type=cash) are skipped. Dollar fields left alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import text

from app import db


def _round_shares(x: float) -> float:
    return round(float(x) * 1e6) / 1e6


def _round_price(x: float) -> float:
    return round(float(x) * 1e6) / 1e6


def ensure_audit_table() -> None:
    dialect = db.engine.dialect.name
    if dialect == "sqlite":
        db.session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS stock_split_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    ratio FLOAT NOT NULL,
                    applied_at TIMESTAMP NOT NULL,
                    UNIQUE(user_id, ratio)
                )
                """
            )
        )
    else:
        db.session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS stock_split_events (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    ratio DOUBLE PRECISION NOT NULL,
                    applied_at TIMESTAMP NOT NULL,
                    UNIQUE(user_id, ratio)
                )
                """
            )
        )
    db.session.commit()


def already_applied(user_id: int, ratio: float) -> bool:
    ensure_audit_table()
    row = db.session.execute(
        text(
            "SELECT id FROM stock_split_events WHERE user_id = :uid AND ratio = :r LIMIT 1"
        ),
        {"uid": user_id, "r": ratio},
    ).fetchone()
    return row is not None


def record_applied(user_id: int, ratio: float) -> None:
    db.session.execute(
        text(
            "INSERT INTO stock_split_events (user_id, ratio, applied_at) "
            "VALUES (:uid, :r, :ts)"
        ),
        {"uid": user_id, "r": ratio, "ts": datetime.utcnow()},
    )


def apply_stock_split_for_user(
    user, ratio: float = 5.0, force: bool = False, commit: bool = True
) -> dict:
    """
    Apply split restatement for one user.
    Commits when commit=True (default). Raises RuntimeError if already applied
    and force is False.
    """
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent
    from app.models.stock_sale import (
        StockSale,
        ISOExercise,
        StockPriceScenario,
        ScenarioPricePoint,
    )
    from app.models.user_price import UserPrice
    from app.utils.encryption import decrypt_for_user, encrypt_for_user, EncryptionError

    if ratio <= 0:
        raise ValueError("ratio must be positive")

    ensure_audit_table()
    if already_applied(user.id, ratio) and not force:
        raise RuntimeError(
            f"Split ratio {ratio}:1 already applied for user_id={user.id}. "
            "Pass force=True only if you intend to re-apply."
        )

    stats = {
        "grants": 0,
        "vests": 0,
        "sales": 0,
        "exercises": 0,
        "user_prices": 0,
        "scenario_points": 0,
        "cash_grants_skipped": 0,
        "price_errors": 0,
    }

    grants = Grant.query.filter_by(user_id=user.id).all()

    for grant in grants:
        if grant.share_type == "cash":
            stats["cash_grants_skipped"] += 1
            continue

        grant.share_quantity = _round_shares((grant.share_quantity or 0) * ratio)
        grant.share_price_at_grant = _round_price((grant.share_price_at_grant or 0) / ratio)
        stats["grants"] += 1

        for vest in VestEvent.query.filter_by(grant_id=grant.id).all():
            vest.shares_vested = _round_shares((vest.shares_vested or 0) * ratio)
            vest.shares_sold = _round_shares((vest.shares_sold or 0) * ratio)
            stats["vests"] += 1

    for sale in StockSale.query.filter_by(user_id=user.id).all():
        sale.shares_sold = _round_shares((sale.shares_sold or 0) * ratio)
        sale.sale_price = _round_price((sale.sale_price or 0) / ratio)
        sale.cost_basis_per_share = _round_price((sale.cost_basis_per_share or 0) / ratio)
        stats["sales"] += 1

    for ex in ISOExercise.query.filter_by(user_id=user.id).all():
        ex.shares_exercised = _round_shares((ex.shares_exercised or 0) * ratio)
        ex.shares_still_held = _round_shares((ex.shares_still_held or 0) * ratio)
        if ex.shares_surrendered is not None:
            ex.shares_surrendered = _round_shares(ex.shares_surrendered * ratio)
        ex.strike_price = _round_price((ex.strike_price or 0) / ratio)
        ex.fmv_at_exercise = _round_price((ex.fmv_at_exercise or 0) / ratio)
        ex.bargain_element_per_share = _round_price(
            (ex.fmv_at_exercise or 0) - (ex.strike_price or 0)
        )
        ex.total_bargain_element = _round_price(
            (ex.shares_exercised or 0) * (ex.bargain_element_per_share or 0)
        )
        stats["exercises"] += 1

    try:
        user_key = user.get_decrypted_user_key()
    except EncryptionError as e:
        raise RuntimeError(
            f"Cannot decrypt user encryption key — refuse to apply without "
            f"adjusting prices: {e}"
        ) from e

    for up in UserPrice.query.filter_by(user_id=user.id).all():
        try:
            price = float(decrypt_for_user(user_key, up.encrypted_price))
            new_price = _round_price(price / ratio)
            up.encrypted_price = encrypt_for_user(user_key, str(new_price))
            stats["user_prices"] += 1
        except Exception:
            stats["price_errors"] += 1

    scenarios = StockPriceScenario.query.filter_by(user_id=user.id).all()
    scenario_ids = [s.id for s in scenarios]
    if scenario_ids:
        points = ScenarioPricePoint.query.filter(
            ScenarioPricePoint.scenario_id.in_(scenario_ids)
        ).all()
        for pt in points:
            pt.price = _round_price((pt.price or 0) / ratio)
            stats["scenario_points"] += 1

    if not already_applied(user.id, ratio):
        record_applied(user.id, ratio)

    if commit:
        db.session.commit()
    return stats
