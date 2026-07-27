"""
Helper utilities for retrieving decrypted user stock prices.

Centralizes the logic used throughout the app to find the most recent
`UserPrice` for a user (optionally as-of a specific date) and decrypt it
using the user's per-user key.

Results are cached per-request so hot pages (dashboard, finance deep dive)
do not re-query and re-decrypt the same price dozens of times.
"""
from __future__ import annotations

from typing import Optional
from datetime import date
import logging

from flask import g, has_request_context
from flask_login import current_user

from app.models.user_price import UserPrice
from app.utils.encryption import decrypt_for_user

logger = logging.getLogger(__name__)


def _cache_get(key):
    if not has_request_context():
        return None, False
    cache = g.setdefault('_user_price_cache', {})
    if key in cache:
        return cache[key], True
    return None, False


def _cache_set(key, value):
    if has_request_context():
        g.setdefault('_user_price_cache', {})[key] = value


def get_latest_user_price(user_id: int, as_of_date: Optional[date] = None) -> Optional[float]:
    """Return the latest decrypted user price for ``user_id`` on or before
    ``as_of_date``. If ``as_of_date`` is None, returns the latest price on or before today.

    Returns a float price on success or None when no price is found or
    decryption fails. Requires the requesting user to be authenticated and
    own the price row (uses ``current_user.get_decrypted_user_key()``).
    """
    effective_date = as_of_date if as_of_date is not None else date.today()
    cache_key = (user_id, effective_date.isoformat())

    cached, hit = _cache_get(cache_key)
    if hit:
        return cached

    try:
        price_entry = (
            UserPrice.query
            .filter_by(user_id=user_id)
            .filter(UserPrice.valuation_date <= effective_date)
            .order_by(UserPrice.valuation_date.desc())
            .first()
        )

        if not price_entry:
            logger.debug(
                "No UserPrice entry found for user %s on or before %s",
                user_id, effective_date,
            )
            _cache_set(cache_key, None)
            return None

        if not current_user.is_authenticated or current_user.id != user_id:
            logger.warning(
                "Attempt to decrypt user price for user %s while %s is authenticated",
                user_id, getattr(current_user, 'id', None),
            )
            return None

        user_key = current_user.get_decrypted_user_key()
        price_str = decrypt_for_user(user_key, price_entry.encrypted_price)
        price = float(price_str)
        _cache_set(cache_key, price)
        return price

    except Exception as e:
        logger.error(
            "Failed to retrieve/decrypt price for user %s: %s",
            user_id, e, exc_info=True,
        )
        return None
