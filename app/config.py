"""
Secure configuration management for the application.
"""

import os
import secrets


class Config:
    """Base configuration with security hardening."""
    
    # Security: Generate strong secret key
    SECRET_KEY = os.getenv('SECRET_KEY')
    if not SECRET_KEY:
        # In production, this should fail. In development, generate one.
        if os.getenv('FLASK_ENV') == 'production':
            raise ValueError(
                "SECRET_KEY must be set in environment variables for production! "
                "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        else:
            # Development only - generate random key
            SECRET_KEY = secrets.token_hex(32)
            print("⚠️  WARNING: Using auto-generated SECRET_KEY for development")
            print("⚠️  Set SECRET_KEY in .env for production!")
    
    # Database
    _db_url = os.getenv('DATABASE_URL', 'sqlite:///stonks.db')
    # Railway / Heroku sometimes provide postgres:// which SQLAlchemy rejects
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,  # Verify connections before using
        'pool_recycle': 300,     # Recycle connections every 5 minutes
    }

    # Public market prices (SpaceX IPO cutover)
    # From first trading day forward, valuations use live/public market data.
    # Manual encrypted user prices remain the source of truth before this date.
    STOCK_TICKER = os.getenv('STOCK_TICKER', 'SPCX')
    PUBLIC_MARKET_START = os.getenv('PUBLIC_MARKET_START', '2026-06-12')  # first SPCX trading day
    MARKET_SYNC_MINUTES = int(os.getenv('MARKET_SYNC_MINUTES', '15'))

    
    # Session Security
    SESSION_COOKIE_SECURE = os.getenv('FLASK_ENV') == 'production'  # HTTPS only in prod
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access
    SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour session timeout
    
    # Remember Me Cookie Security
    REMEMBER_COOKIE_SECURE = os.getenv('FLASK_ENV') == 'production'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = 86400 * 30  # 30 days
    
    # CSRF Protection
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour
    WTF_CSRF_SSL_STRICT = os.getenv('FLASK_ENV') == 'production'
    
    # Security Headers (Flask-Talisman)
    TALISMAN_FORCE_HTTPS = os.getenv('FLASK_ENV') == 'production'
    TALISMAN_STRICT_TRANSPORT_SECURITY = True
    TALISMAN_STRICT_TRANSPORT_SECURITY_MAX_AGE = 31536000  # 1 year
    TALISMAN_CONTENT_SECURITY_POLICY = {
        'default-src': "'self'",
        'script-src': ["'self'", "'unsafe-inline'"],  # TODO: Remove unsafe-inline
        'style-src': ["'self'", "'unsafe-inline'"],   # TODO: Remove unsafe-inline
        'img-src': ["'self'", 'data:', 'https:'],
        'font-src': ["'self'", 'data:'],
        'connect-src': "'self'",
        'frame-ancestors': "'none'",
        'base-uri': "'self'",
        'form-action': "'self'"
    }
    
    # Mail Configuration
    MAIL_SERVER = os.getenv('MAIL_SERVER')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'True') == 'True'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER')
    
    # Password Policy
    PASSWORD_MIN_LENGTH = 12
    PASSWORD_REQUIRE_UPPERCASE = True
    PASSWORD_REQUIRE_LOWERCASE = True
    PASSWORD_REQUIRE_DIGIT = True
    PASSWORD_REQUIRE_SPECIAL = True
    PASSWORD_MAX_LENGTH = 128
    
    # File Upload Security (if needed in future)
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
    
    # Audit Logging
    AUDIT_LOG_FILE = os.getenv('AUDIT_LOG_FILE', 'logs/audit.log')
    SECURITY_LOG_FILE = os.getenv('SECURITY_LOG_FILE', 'logs/security.log')
    
    # Development/Production Flags
    DEBUG = os.getenv('FLASK_ENV') != 'production'
    TESTING = False


class ProductionConfig(Config):
    """Production-specific security hardening."""
    DEBUG = False
    TESTING = False
    
    # Enforce HTTPS
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    TALISMAN_FORCE_HTTPS = True
    
    # Stricter session timeout
    PERMANENT_SESSION_LIFETIME = 1800  # 30 minutes


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    
    # More lenient for development
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    TALISMAN_FORCE_HTTPS = False


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    WTF_CSRF_ENABLED = False  # Disable for testing
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config():
    """Get configuration based on environment."""
    env = os.getenv('FLASK_ENV', 'development')
    return config.get(env, config['default'])
