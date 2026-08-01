"""Private pre-IPO prices must load in background (no login) for RSU basis."""

import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app


def test_user_key_for_private_prices_background_path():
    app = create_app()
    with app.app_context():
        from app.utils import price_utils

        fake_user = MagicMock()
        fake_user.get_decrypted_user_key.return_value = b'fake-fernet-key-32b-padding!!!!!'

        with patch.object(price_utils, 'has_request_context', return_value=False):
            with patch('app.models.user.User') as UserMod:
                UserMod.query.get.return_value = fake_user
                key = price_utils._user_key_for_private_prices(42)
        assert key == b'fake-fernet-key-32b-padding!!!!!'
        fake_user.get_decrypted_user_key.assert_called_once()


def test_user_key_blocks_cross_user_in_request():
    app = create_app()
    with app.app_context():
        from app.utils import price_utils

        other = MagicMock()
        other.is_authenticated = True
        other.id = 99

        with patch.object(price_utils, 'has_request_context', return_value=True):
            with patch.object(price_utils, 'current_user', other):
                key = price_utils._user_key_for_private_prices(1)
        assert key is None
