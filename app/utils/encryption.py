"""
Encryption utilities for per-user data encryption.

Uses a server master Fernet key (VESTX_MASTER_KEY) to encrypt per-user keys.
Per-user keys (Fernet) are used to encrypt/decrypt user-specific values (stock price entries).

Security notes:
- VESTX_MASTER_KEY must be a valid Fernet key (base64 urlsafe, 44 chars). Set it in the environment.
- Example (zsh):
  export VESTX_MASTER_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

This module exposes helpers to generate per-user keys and to encrypt/decrypt blobs.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
import os
from typing import Optional


class EncryptionError(Exception):
    pass


def _load_master_key() -> bytes:
    """Load the server master key from VESTX_MASTER_KEY environment variable.

    Raises:
        EncryptionError: if key is missing or invalid.
    """
    key = os.getenv('VESTX_MASTER_KEY')
    if not key:
        raise EncryptionError(
            'VESTX_MASTER_KEY not set. Generate with: '
            "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )

    if isinstance(key, str):
        key_b = key.encode()
    else:
        key_b = key

    # Basic validation: Fernet keys are 44 bytes when base64-encoded
    if len(key_b) not in (44, 43, 44 + 1):
        # still attempt to use it; Fernet will raise if invalid
        pass

    return key_b


def get_master_fernet() -> Fernet:
    """Return a Fernet instance for the master key.

    Raises EncryptionError if master key is missing/invalid.
    """
    key = _load_master_key()
    try:
        return Fernet(key)
    except Exception as e:
        raise EncryptionError(f'Invalid VESTX_MASTER_KEY: {e}')


def generate_user_key() -> bytes:
    """Generate a new per-user Fernet key (bytes)."""
    return Fernet.generate_key()


def encrypt_with_master(plaintext: bytes) -> bytes:
    """Encrypt bytes using the master key. Returns ciphertext bytes."""
    f = get_master_fernet()
    return f.encrypt(plaintext)


def decrypt_with_master(token: bytes) -> bytes:
    """Decrypt bytes using the master key. Returns plaintext bytes.

    Raises EncryptionError for invalid token.
    """
    f = get_master_fernet()
    try:
        return f.decrypt(token)
    except InvalidToken:
        raise EncryptionError('Failed to decrypt with master key (invalid token)')


def encrypt_for_user(user_key: bytes, plaintext: str) -> bytes:
    """Encrypt a UTF-8 string using the provided per-user key (Fernet). Returns ciphertext bytes."""
    if isinstance(user_key, str):
        user_key = user_key.encode()
    f = Fernet(user_key)
    return f.encrypt(plaintext.encode())


def decrypt_for_user(user_key: bytes, token: bytes) -> str:
    """Decrypt token bytes using the provided per-user key and return UTF-8 string.

    Raises EncryptionError on failure.
    """
    if isinstance(user_key, str):
        user_key = user_key.encode()
    f = Fernet(user_key)
    try:
        return f.decrypt(token).decode()
    except InvalidToken:
        raise EncryptionError('Failed to decrypt user token (invalid token)')


def generate_master_key_command() -> str:
    """Return a shell command string to generate and export a new master key (zsh).

    Not executed by the app — provided for operator convenience.
    """
    return (
        "export VESTX_MASTER_KEY=\"$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')\'\""
    )
