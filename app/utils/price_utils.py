"""
Helper utilities for retrieving decrypted user stock prices.

Loads a user's full price history once per request, then answers as-of lookups
from memory (with a secondary per-(user, date) cache).
"""
from __future__ import annotations

from typing import Optional, List, Tuple
from datetime import date
import logging
import bisect

from flask import g, has_request_context
from flask_login import current_user

from app.models.user_price import UserPrice
from app.utils.encryption import decrypt_for_user, EncryptionError

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


def _load_sorted_price_history(user_id: int) -> List[Tuple[date, float]]:
    """
    Load and decrypt all prices for user_id, sorted by valuation_date ascending.
    Cached on flask.g for the request lifetime.
    """
    if has_request_context():
        histories = g.setdefault('_user_price_histories', {})
        if user_id in histories:
            return histories[user_id]

    if not current_user.is_authenticated or current_user.id != user_id:
        logger.warning(
            "Attempt to decrypt user price for user %s while %s is authenticated",
            user_id, getattr(current_user, 'id', None),
        )
        history: List[Tuple[date, float]] = []
        if has_request_context():
            g.setdefault('_user_price_histories', {})[user_id] = history
        return history

    try:
        user_key = current_user.get_decrypted_user_key()
    except EncryptionError:
        logger.error("Cannot load price history for user %s: encryption key failure", user_id)
        history = []
        if has_request_context():
            g.setdefault('_user_price_histories', {})[user_id] = history
        return history

    entries = (
        UserPrice.query
        .filter_by(user_id=user_id)
        .order_by(UserPrice.valuation_date.asc())
        .all()
    )

    history = []
    for entry in entries:
        try:
            price = float(decrypt_for_user(user_key, entry.encrypted_price))
            history.append((entry.valuation_date, price))
        except Exception as e:
            logger.warning(
                "Skipping undecryptable price id=%s for user %s: %s",
                entry.id, user_id, e,
            )
            continue

    if has_request_context():
        g.setdefault('_user_price_histories', {})[user_id] = history
    return history


def get_latest_user_price(user_id: int, as_of_date: Optional[date] = None) -> Optional[float]:
    """Return the latest decrypted user price for ``user_id`` on or before
    ``as_of_date``. If ``as_of_date`` is None, returns the latest price on or before today.

    Returns a float price on success or None when no price is found or
    decryption fails. Requires the requesting user to be authenticated and
    own the price row.
    """
    effective_date = as_of_date if as_of_date is not None else date.today()
    cache_key = (user_id, effective_date.isoformat())

    cached, hit = _cache_get(cache_key)
    if hit:
        return cached

    try:
        history = _load_sorted_price_history(user_id)
        if not history:
            _cache_set(cache_key, None)
            return None

        # Bisect for last price on or before effective_date
        dates = [d for d, _ in history]
        idx = bisect.bisect_right(dates, effective_date) - 1
        if idx < 0:
            _cache_set(cache_key, None)
            return None

        price = history[idx][1]
        _cache_set(cache_key, price)
        return price

    except Exception as e:
        logger.error(
            "Failed to retrieve/decrypt price for user %s: %s",
            user_id, e, exc_info=True,
        )
        return None


def warm_user_price_history(user_id: int) -> int:
    """
    Eager-load a user's full price history into the request cache.
    Returns number of price points loaded. Call from hot multi-vest pages.
    """
    history = _load_sorted_price_history(user_id)
    return len(history)
