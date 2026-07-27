"""
Helper utilities for stock prices.

Pre-IPO (before PUBLIC_MARKET_START): per-user encrypted valuations.
From first public trading day onward: shared SPCX market prices (Yahoo/Stooq).
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


def _public_start() -> date:
    from app.utils.market_data import public_market_start
    return public_market_start()


def _load_private_history(user_id: int) -> List[Tuple[date, float]]:
    """Decrypt user-entered prices strictly before public market start."""
    if not current_user.is_authenticated or current_user.id != user_id:
        logger.warning(
            "Attempt to decrypt user price for user %s while %s is authenticated",
            user_id, getattr(current_user, 'id', None),
        )
        return []

    try:
        user_key = current_user.get_decrypted_user_key()
    except EncryptionError:
        logger.error("Cannot load private prices for user %s: encryption key failure", user_id)
        return []

    cutover = _public_start()
    entries = (
        UserPrice.query
        .filter_by(user_id=user_id)
        .filter(UserPrice.valuation_date < cutover)
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
    return history


def _load_sorted_price_history(user_id: int) -> List[Tuple[date, float]]:
    """
    Merged history: private pre-IPO + public post-IPO, sorted by date.
    Cached per request on flask.g.
    """
    if has_request_context():
        histories = g.setdefault('_user_price_histories', {})
        if user_id in histories:
            return histories[user_id]

    private = _load_private_history(user_id)

    public: List[Tuple[date, float]] = []
    try:
        from app.utils.market_data import load_public_price_history
        public = load_public_price_history(ensure_sync=True)
    except Exception as e:
        logger.warning("Public market history unavailable: %s", e)

    # Private only before cutover; public from cutover onward (public wins ties)
    cutover = _public_start()
    merged: dict = {}
    for d, p in private:
        if d < cutover:
            merged[d] = p
    for d, p in public:
        if d >= cutover:
            merged[d] = p

    history = sorted(merged.items(), key=lambda x: x[0])

    if has_request_context():
        g.setdefault('_user_price_histories', {})[user_id] = history
    return history


def get_latest_user_price(user_id: int, as_of_date: Optional[date] = None) -> Optional[float]:
    """Return the latest price for ``user_id`` on or before ``as_of_date``.

    Uses private encrypted history before the IPO trading cutover, and public
    SPCX market data from the first trading day forward.
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
            "Failed to retrieve price for user %s: %s",
            user_id, e, exc_info=True,
        )
        return None


def warm_user_price_history(user_id: int) -> int:
    """Eager-load merged price history into the request cache."""
    history = _load_sorted_price_history(user_id)
    return len(history)


def get_merged_price_series(user_id: int) -> List[Tuple[date, float]]:
    """Full merged series for charts (date, price)."""
    return list(_load_sorted_price_history(user_id))
