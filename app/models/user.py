"""
User model for authentication and user management.
Enhanced with security features for account protection.
"""

from app import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import secrets


class User(UserMixin, db.Model):
    """User model for authentication with enhanced security."""
    
    __tablename__ = 'users'
    
    # Core fields
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Security fields
    failed_login_attempts = db.Column(db.Integer, default=0)
    is_locked = db.Column(db.Boolean, default=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)
    last_password_change = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Password reset fields
    password_reset_token = db.Column(db.String(255), nullable=True)
    password_reset_expiry = db.Column(db.DateTime, nullable=True)
    
    # Email verification
    email_verified = db.Column(db.Boolean, default=False)
    email_verification_token = db.Column(db.String(255), nullable=True)
    
    # Two-factor authentication
    totp_secret = db.Column(db.String(32), nullable=True)
    totp_enabled = db.Column(db.Boolean, default=False)
    backup_codes = db.Column(db.Text, nullable=True)  # JSON array of hashed codes
    
    # Session security
    session_token = db.Column(db.String(255), nullable=True)
    
    # Per-user encrypted key (Fernet) - stored encrypted by server master key
    encrypted_user_key = db.Column(db.LargeBinary, nullable=True)

    # Optional per-user xAI / Grok API key (encrypted with the user's Fernet key)
    # Never store plaintext; never return full key to the client after save.
    encrypted_xai_api_key = db.Column(db.LargeBinary, nullable=True)
    xai_model = db.Column(db.String(64), nullable=True)  # e.g. grok-4.6; null = current flagship
    
    # Tax preferences (simplified approach)
    federal_tax_rate = db.Column(db.Float, default=0.22)  # Default to 22% bracket
    state_tax_rate = db.Column(db.Float, default=0.0)     # Default to 0% (user sets based on their state)
    include_fica = db.Column(db.Boolean, default=True)    # Include FICA in tax estimates
    ss_wage_base_maxed = db.Column(db.Boolean, default=False)  # If True, only Medicare applies (no SS tax)
    
    # Relationships
    grants = db.relationship('Grant', backref='user', lazy=True, cascade='all, delete-orphan')
    prices = db.relationship('UserPrice', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password: str) -> None:
        """
        Hash and set the user's password.
        Also updates last_password_change timestamp.
        """
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256:600000')
        self.last_password_change = datetime.utcnow()
    
    def check_password(self, password: str) -> bool:
        """Check if provided password matches the hash."""
        return check_password_hash(self.password_hash, password)
    
    def is_account_locked(self) -> bool:
        """Check if account is currently locked."""
        if not self.is_locked:
            return False
        
        # Check if temporary lock has expired
        if self.locked_until and datetime.utcnow() > self.locked_until:
            self.is_locked = False
            self.locked_until = None
            self.failed_login_attempts = 0
            db.session.commit()
            return False
        
        return True
    
    def generate_password_reset_token(self) -> str:
        """Generate a secure password reset token."""
        token = secrets.token_urlsafe(32)
        self.password_reset_token = generate_password_hash(token)
        return token
    
    def verify_password_reset_token(self, token: str) -> bool:
        """Verify password reset token."""
        if not self.password_reset_token:
            return False
        return check_password_hash(self.password_reset_token, token)
    
    def generate_email_verification_token(self) -> str:
        """Generate a secure email verification token."""
        token = secrets.token_urlsafe(32)
        self.email_verification_token = generate_password_hash(token)
        return token
    
    def verify_email_token(self, token: str) -> bool:
        """Verify email verification token."""
        if not self.email_verification_token:
            return False
        return check_password_hash(self.email_verification_token, token)
    
    def generate_totp_secret(self) -> str:
        """Generate TOTP secret for 2FA."""
        import pyotp
        secret = pyotp.random_base32()
        self.totp_secret = secret
        return secret
    
    def verify_totp(self, token: str) -> bool:
        """Verify TOTP token."""
        if not self.totp_enabled or not self.totp_secret:
            return False
        
        import pyotp
        totp = pyotp.TOTP(self.totp_secret)
        return totp.verify(token, valid_window=1)
    
    # Encryption helpers for per-user key
    def ensure_encryption_key(self) -> bytes:
        """
        Return the user's per-user Fernet key (bytes).

        - If no key exists yet (new user), generate and store one once.
        - If a key exists but cannot be decrypted, raise EncryptionError and
          do NOT regenerate. Regenerating would permanently orphan encrypted
          UserPrice rows under the old key.
        """
        from app.utils.encryption import (
            decrypt_with_master,
            generate_user_key,
            encrypt_with_master,
            EncryptionError,
        )
        import logging
        logger = logging.getLogger(__name__)

        if self.encrypted_user_key:
            try:
                return decrypt_with_master(self.encrypted_user_key)
            except Exception as e:
                logger.error(
                    "Failed to decrypt encrypted_user_key for user %s: %s",
                    self.id, e, exc_info=True,
                )
                raise EncryptionError(
                    f"Cannot decrypt encryption key for user {self.id}. "
                    "Verify VESTX_MASTER_KEY matches the key used when prices were saved. "
                    "Refusing to regenerate the user key to protect existing encrypted data."
                ) from e

        # Brand-new user only: create key for the first time
        logger.info("Creating first encryption key for user %s", self.id)
        user_key = generate_user_key()
        self.encrypted_user_key = encrypt_with_master(user_key)
        db.session.add(self)
        db.session.commit()
        return user_key

    def get_decrypted_user_key(self) -> bytes:
        """Return decrypted per-user key. Creates one only if none exists yet."""
        return self.ensure_encryption_key()

    def set_encrypted_user_key(self, encrypted_blob: bytes) -> None:
        """Directly set the encrypted_user_key (blob). Use only for recovery tooling."""
        self.encrypted_user_key = encrypted_blob
        db.session.add(self)
        db.session.commit()

    def has_xai_api_key(self) -> bool:
        try:
            return bool(self.encrypted_xai_api_key)
        except Exception:
            # Column missing until migration runs
            return False

    def set_xai_api_key(self, api_key: str) -> None:
        """Encrypt and store the user's xAI API key. Empty string clears it."""
        from app.utils.encryption import encrypt_for_user

        raw = (api_key or '').strip()
        if not raw:
            self.encrypted_xai_api_key = None
            return
        user_key = self.get_decrypted_user_key()
        self.encrypted_xai_api_key = encrypt_for_user(user_key, raw)

    def clear_xai_api_key(self) -> None:
        self.encrypted_xai_api_key = None

    def get_xai_api_key(self):
        """Decrypt API key for server-side Grok calls only. Never send to templates."""
        if not self.encrypted_xai_api_key:
            return None
        from app.utils.encryption import decrypt_for_user, EncryptionError
        try:
            user_key = self.get_decrypted_user_key()
            return decrypt_for_user(user_key, self.encrypted_xai_api_key)
        except EncryptionError:
            return None

    def xai_key_hint(self):
        """Masked hint for UI, e.g. xai-...abcd — never the full secret."""
        key = self.get_xai_api_key()
        if not key:
            return None
        if len(key) <= 8:
            return '••••••••'
        return f'{key[:4]}…{key[-4:]}'
    
    def get_federal_tax_rate(self) -> float:
        """Get user's federal tax rate (defaults to 22% if not set)."""
        return self.federal_tax_rate if self.federal_tax_rate is not None else 0.22
    
    def get_state_tax_rate(self) -> float:
        """Get user's state tax rate (defaults to 0% if not set)."""
        return self.state_tax_rate if self.state_tax_rate is not None else 0.0
    
    def get_tax_rates(self) -> dict:
        """
        Legacy flat rate display helpers only.

        Money estimates must use tax_engine / sale_tax_estimate / wage_year_tax
        with TaxYearProfile — not these flat User fields.
        """
        if not self.include_fica:
            fica_rate = 0.0
        elif self.ss_wage_base_maxed:
            fica_rate = 0.0145  # Medicare only (SS wage base already maxed)
        else:
            fica_rate = 0.0765  # 6.2% SS + 1.45% Medicare
        
        return {
            'federal': self.get_federal_tax_rate(),
            'state': self.get_state_tax_rate(),
            'fica': fica_rate,
            'total': self.get_federal_tax_rate() + self.get_state_tax_rate() + fica_rate,
            'legacy_flat': True,
        }
    
    def get_total_tax_rate(self) -> float:
        """Get total combined tax rate for estimates."""
        rates = self.get_tax_rates()
        return rates['total']
    
    def __repr__(self) -> str:
        return f'<User {self.username}>'

