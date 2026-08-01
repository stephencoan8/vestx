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


def _user_key_for_private_prices(user_id: int) -> Optional[bytes]:
    """
    Decrypt the user's Fernet key for private pre-IPO prices.

    HTTP: only the authenticated owner may decrypt (no cross-user reads).
    Background jobs (no request / no login): load via master key for that user_id
    so RSU cost basis (FMV at vest) is not zeroed when Grok/goal engines run async.
    """
    # Active browser session: enforce ownership
    if has_request_context():
        try:
            authed = bool(getattr(current_user, 'is_authenticated', False))
            cuid = getattr(current_user, 'id', None) if authed else None
        except Exception:
            authed, cuid = False, None

        if not authed:
            return None
        if cuid != user_id:
            logger.warning(
                "Blocked private price decrypt for user %s while %s is authenticated",
                user_id, cuid,
            )
            return None
        try:
            return current_user.get_decrypted_user_key()
        except EncryptionError:
            logger.error("Cannot load private prices for user %s: encryption key failure", user_id)
            return None
        except Exception as e:
            logger.error("Cannot load private prices for user %s: %s", user_id, e)
            return None

    # No request context (advisor job thread, CLI, etc.): server-side master key
    try:
        from app.models.user import User
        user = User.query.get(user_id)
        if not user:
            return None
        return user.get_decrypted_user_key()
    except EncryptionError:
        logger.error("Background private price decrypt failed for user %s: key error", user_id)
        return None
    except Exception as e:
        logger.error("Background private price decrypt failed for user %s: %s", user_id, e)
        return None


def _load_private_history(user_id: int) -> List[Tuple[date, float]]:
    """Decrypt user-entered prices strictly before public market start."""
    user_key = _user_key_for_private_prices(user_id)
    if not user_key:
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
    cache_key = (user_id, effective_date.isoformat(), 'as_of')

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


def get_price_on_or_near_date(
    user_id: int,
    on_date: date,
    *,
    allow_forward_fill: bool = True,
) -> tuple:
    """
    Price for basis / vest FMV lookups.

    Prefer last known price on or before ``on_date``. If none exists and
    ``allow_forward_fill``, use the earliest later price (planning fallback —
    never invent a number from thin air, but also never silently use $0 when
    history exists only after the vest).

    Returns (price_or_None, source) with source in {'as_of','forward_fill','missing'}.
    """
    if on_date is None:
        return None, 'missing'
    cache_key = (user_id, on_date.isoformat(), 'near', bool(allow_forward_fill))
    cached, hit = _cache_get(cache_key)
    if hit:
        return cached

    try:
        history = _load_sorted_price_history(user_id)
        if not history:
            result = (None, 'missing')
            _cache_set(cache_key, result)
            return result

        dates = [d for d, _ in history]
        idx = bisect.bisect_right(dates, on_date) - 1
        if idx >= 0:
            result = (float(history[idx][1]), 'as_of')
            _cache_set(cache_key, result)
            return result
        if allow_forward_fill:
            # All history is after on_date — take earliest available
            result = (float(history[0][1]), 'forward_fill')
            _cache_set(cache_key, result)
            return result
        result = (None, 'missing')
        _cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.error(
            "Failed near-date price for user %s on %s: %s",
            user_id, on_date, e, exc_info=True,
        )
        return None, 'missing'


def warm_user_price_history(user_id: int) -> int:
    """Eager-load merged price history into the request cache."""
    history = _load_sorted_price_history(user_id)
    return len(history)


def get_merged_price_series(user_id: int) -> List[Tuple[date, float]]:
    """Full merged series for charts (date, price)."""
    return list(_load_sorted_price_history(user_id))
