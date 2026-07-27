#!/usr/bin/env python3
"""
One-off stock split restatement for a single user.

Default is dry-run (no writes). Example:

  # Inspect only
  python scripts/apply_stock_split.py --user stephencoan --ratio 5

  # Apply after review
  python scripts/apply_stock_split.py --user stephencoan --ratio 5 --apply

Requires DATABASE_URL and VESTX_MASTER_KEY in the environment (same as the app).

Rules (5:1 example):
  - Equity share counts × ratio
  - Per-share prices / strike / cost basis per share ÷ ratio
  - Cash grants (share_type=cash) skipped — those fields are USD
  - Dollar aggregates (cash_paid, total_proceeds, capital_gain, etc.) left alone
    (or recomputed where they are pure shares×price products)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

# Project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _round_shares(x: float) -> float:
    return round(float(x) * 1e6) / 1e6


def _round_price(x: float) -> float:
    return round(float(x) * 1e6) / 1e6


def _ensure_audit_table(db):
    from sqlalchemy import text

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


def _already_applied(db, user_id: int, ratio: float) -> bool:
    from sqlalchemy import text

    row = db.session.execute(
        text(
            "SELECT id FROM stock_split_events WHERE user_id = :uid AND ratio = :r LIMIT 1"
        ),
        {"uid": user_id, "r": ratio},
    ).fetchone()
    return row is not None


def _record_applied(db, user_id: int, ratio: float) -> None:
    from sqlalchemy import text

    db.session.execute(
        text(
            "INSERT INTO stock_split_events (user_id, ratio, applied_at) "
            "VALUES (:uid, :r, :ts)"
        ),
        {"uid": user_id, "r": ratio, "ts": datetime.utcnow()},
    )


def _snapshot_equity_totals(user, grants) -> dict:
    """Rough portfolio snapshot using latest stored price if decryptable."""
    from app.models.user_price import UserPrice
    from app.utils.encryption import decrypt_for_user, EncryptionError

    latest_price = None
    try:
        user_key = user.get_decrypted_user_key()
        entry = (
            UserPrice.query.filter_by(user_id=user.id)
            .order_by(UserPrice.valuation_date.desc())
            .first()
        )
        if entry:
            latest_price = float(decrypt_for_user(user_key, entry.encrypted_price))
    except EncryptionError as e:
        print(f"  (price snapshot skipped: {e})")
    except Exception as e:
        print(f"  (price snapshot skipped: {e})")

    total_shares = 0.0
    total_value = 0.0
    for g in grants:
        if g.share_type == "cash":
            continue
        total_shares += g.share_quantity or 0
        if latest_price is not None:
            if g.share_type in ("iso_5y", "iso_6y"):
                total_value += (g.share_quantity or 0) * (
                    latest_price - (g.share_price_at_grant or 0)
                )
            else:
                total_value += (g.share_quantity or 0) * latest_price

    return {
        "latest_price": latest_price,
        "equity_shares": total_shares,
        "equity_value": total_value,
        "grant_count": len([g for g in grants if g.share_type != "cash"]),
        "cash_grant_count": len([g for g in grants if g.share_type == "cash"]),
    }


def apply_split(user, ratio: float, do_apply: bool) -> dict:
    from app import db
    from app.models.grant import Grant
    from app.models.vest_event import VestEvent
    from app.models.stock_sale import StockSale, ISOExercise, StockPriceScenario, ScenarioPricePoint
    from app.models.user_price import UserPrice
    from app.utils.encryption import decrypt_for_user, encrypt_for_user, EncryptionError

    stats = {
        "grants": 0,
        "vests": 0,
        "sales": 0,
        "exercises": 0,
        "user_prices": 0,
        "scenario_points": 0,
        "cash_grants_skipped": 0,
    }

    grants = Grant.query.filter_by(user_id=user.id).all()
    before = _snapshot_equity_totals(user, grants)
    print("\n=== BEFORE ===")
    print(f"  Equity grants: {before['grant_count']}  (cash skipped: {before['cash_grant_count']})")
    print(f"  Equity shares (sum of grant quantities): {before['equity_shares']:,.4f}")
    if before["latest_price"] is not None:
        print(f"  Latest user price: ${before['latest_price']:,.4f}")
        print(f"  Rough equity value: ${before['equity_value']:,.2f}")

    user_key = None
    try:
        user_key = user.get_decrypted_user_key()
    except EncryptionError as e:
        print(f"\nWARNING: cannot decrypt user key ({e}). user_prices will not be adjusted.")
    except Exception as e:
        print(f"\nWARNING: user key error ({e}). user_prices will not be adjusted.")

    # --- Grants + vests ---
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
            # cash_paid is dollars — leave unchanged
            stats["vests"] += 1

    # --- Stock sales ---
    for sale in StockSale.query.filter_by(user_id=user.id).all():
        sale.shares_sold = _round_shares((sale.shares_sold or 0) * ratio)
        sale.sale_price = _round_price((sale.sale_price or 0) / ratio)
        sale.cost_basis_per_share = _round_price((sale.cost_basis_per_share or 0) / ratio)
        # total_proceeds, total_cost_basis, capital_gain are dollar totals — leave as-is
        # (shares×price products stay the same after ×ratio / ÷ratio)
        stats["sales"] += 1

    # --- ISO exercises ---
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
        # cash_paid / amt_paid are dollars — leave
        stats["exercises"] += 1

    # --- Encrypted user prices ---
    if user_key is not None:
        for up in UserPrice.query.filter_by(user_id=user.id).all():
            try:
                price = float(decrypt_for_user(user_key, up.encrypted_price))
                new_price = _round_price(price / ratio)
                up.encrypted_price = encrypt_for_user(user_key, str(new_price))
                stats["user_prices"] += 1
            except Exception as e:
                print(f"  SKIP user_price id={up.id} date={up.valuation_date}: {e}")

    # --- Scenario price points ---
    scenarios = StockPriceScenario.query.filter_by(user_id=user.id).all()
    scenario_ids = [s.id for s in scenarios]
    if scenario_ids:
        points = ScenarioPricePoint.query.filter(
            ScenarioPricePoint.scenario_id.in_(scenario_ids)
        ).all()
        for pt in points:
            pt.price = _round_price((pt.price or 0) / ratio)
            stats["scenario_points"] += 1

    print("\n=== PLANNED CHANGES ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # After mutation in-session, snapshot (works for dry-run too if we don't rollback yet)
    after = _snapshot_equity_totals(user, grants)
    print("\n=== AFTER (in-session) ===")
    print(f"  Equity shares: {after['equity_shares']:,.4f}  (expect ~{before['equity_shares'] * ratio:,.4f})")
    if after["latest_price"] is not None:
        print(f"  Latest user price: ${after['latest_price']:,.4f}  (expect ~${(before['latest_price'] or 0) / ratio:,.4f})")
        print(f"  Rough equity value: ${after['equity_value']:,.2f}  (expect ~${before['equity_value']:,.2f})")

    if do_apply:
        db.session.add(user)
        _record_applied(db, user.id, ratio)
        db.session.commit()
        print("\nAPPLIED and committed.")
    else:
        db.session.rollback()
        print("\nDRY-RUN only — rolled back. Re-run with --apply to commit.")

    return {"before": before, "after": after, "stats": stats}


def main():
    parser = argparse.ArgumentParser(description="Apply a stock-split restatement for one user")
    parser.add_argument("--user", required=True, help="Username or numeric user id")
    parser.add_argument("--ratio", type=float, default=5.0, help="Split ratio (default 5 for 5:1)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes (default is dry-run / rollback)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow re-run even if this ratio was already recorded for the user",
    )
    args = parser.parse_args()

    if args.ratio <= 0:
        print("ratio must be positive")
        sys.exit(1)

    from app import create_app, db
    from app.models.user import User

    app = create_app()
    with app.app_context():
        _ensure_audit_table(db)

        if args.user.isdigit():
            user = User.query.get(int(args.user))
        else:
            user = User.query.filter_by(username=args.user).first()

        if not user:
            print(f"User not found: {args.user}")
            sys.exit(1)

        print(f"User: id={user.id} username={user.username}")
        print(f"Ratio: {args.ratio}:1  mode={'APPLY' if args.apply else 'DRY-RUN'}")

        if _already_applied(db, user.id, args.ratio) and not args.force:
            print(
                f"\nRefusing: stock_split_events already has ratio={args.ratio} "
                f"for user_id={user.id}. Use --force only if you intend to re-apply."
            )
            sys.exit(2)

        apply_split(user, args.ratio, do_apply=args.apply)


if __name__ == "__main__":
    main()
