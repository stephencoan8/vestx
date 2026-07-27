#!/usr/bin/env python3
"""
CLI for one-off stock split restatement (single user).

  python scripts/apply_stock_split.py --user YOUR_USERNAME --ratio 5 --apply

Requires DATABASE_URL and VESTX_MASTER_KEY. Prefer the Admin UI on Railway
if you only need to adjust the logged-in admin account.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description="Apply stock-split restatement for one user")
    parser.add_argument("--user", required=True, help="Username or numeric user id")
    parser.add_argument("--ratio", type=float, default=5.0)
    parser.add_argument("--apply", action="store_true", help="Commit (default is dry-run)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    from app import create_app, db
    from app.models.user import User
    from app.utils.stock_split import apply_stock_split_for_user, already_applied

    app = create_app()
    with app.app_context():
        if args.user.isdigit():
            user = User.query.get(int(args.user))
        else:
            user = User.query.filter_by(username=args.user).first()
        if not user:
            print(f"User not found: {args.user}")
            sys.exit(1)

        print(f"User: id={user.id} username={user.username} ratio={args.ratio}")

        if already_applied(user.id, args.ratio) and not args.force:
            print("Already applied. Use --force to override.")
            sys.exit(2)

        if not args.apply:
            print("Dry-run only (no commit). Pass --apply to write.")
            try:
                stats = apply_stock_split_for_user(
                    user, ratio=args.ratio, force=True, commit=False
                )
                db.session.rollback()
                print("Would apply:", stats)
                print("DRY-RUN rolled back.")
            except Exception as e:
                db.session.rollback()
                print("Dry-run failed:", e)
                sys.exit(1)
            return

        stats = apply_stock_split_for_user(
            user, ratio=args.ratio, force=args.force, commit=True
        )
        print("APPLIED:", stats)


if __name__ == "__main__":
    main()
