"""
Helper utilities for retrieving decrypted user stock prices.

Centralizes the logic used throughout the app to find the most recent
`UserPrice` for a user (optionally as-of a specific date) and decrypt it
using the user's per-user key.

This mirrors the behavior used in `VestEvent.share_price_at_vest` and
ensures consistent behaviour across grant creation, views, and finance code.
"""
from __future__ import annotations

from typing import Optional
from datetime import date
import logging

from flask_login import current_user

from app.models.user_price import UserPrice
from app.utils.encryption import decrypt_for_user

logger = logging.getLogger(__name__)


def get_latest_user_price(user_id: int, as_of_date: Optional[date] = None) -> Optional[float]:
    """Return the latest decrypted user price for ``user_id`` on or before
    ``as_of_date``. If ``as_of_date`` is None, returns the latest price on or before today.

    Returns a float price on success or None when no price is found or
    decryption fails. This intentionally requires the requesting user to be
    authenticated (uses ``current_user.get_decrypted_user_key()``) just like
    the existing model properties that decrypt prices.
    """
    try:
        from datetime import date as date_class
        
        # Find the appropriate price entry
        query = UserPrice.query.filter_by(user_id=user_id)
        
        # Always filter to on or before a specific date
        # If no as_of_date provided, use today to exclude future prices
        if as_of_date is not None:
            query = query.filter(UserPrice.valuation_date <= as_of_date)
        else:
            query = query.filter(UserPrice.valuation_date <= date_class.today())
            
        price_entry = query.order_by(UserPrice.valuation_date.desc()).first()

        if not price_entry:
            logger.debug("No UserPrice entry found for user %s on or before %s", user_id, as_of_date or "today")
            return None

        if not current_user.is_authenticated or current_user.id != user_id:
            logger.warning("Attempt to decrypt user price for user %s while %s is authenticated", user_id, getattr(current_user, 'id', None))
            return None

        user_key = current_user.get_decrypted_user_key()
        price_str = decrypt_for_user(user_key, price_entry.encrypted_price)
        return float(price_str)

    except Exception as e:
        logger.error("Failed to retrieve/decrypt price for user %s: %s", user_id, e, exc_info=True)
        return None
