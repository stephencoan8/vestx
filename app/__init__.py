"""
Application factory and initialization.
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from flask_talisman import Talisman
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()
csrf = CSRFProtect()
talisman = Talisman()


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)
    
    # Load secure configuration
    from app.config import get_config
    app.config.from_object(get_config())
    
    # Initialize extensions with app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.session_protection = 'strong'  # Enhanced session protection
    mail.init_app(app)
    csrf.init_app(app)

    # Make csrf_token available in all templates for manual forms
    @app.context_processor
    def inject_csrf_token():
        from flask_wtf.csrf import generate_csrf
        return dict(csrf_token=generate_csrf)
    
    # Initialize Talisman with security headers
    if app.config.get('TALISMAN_FORCE_HTTPS'):
        talisman.init_app(
            app,
            force_https=True,
            strict_transport_security=True,
            strict_transport_security_max_age=31536000,
            content_security_policy=app.config.get('TALISMAN_CONTENT_SECURITY_POLICY'),
            content_security_policy_nonce_in=['script-src'],
            feature_policy={
                'geolocation': "'none'",
                'camera': "'none'",
                'microphone': "'none'"
            }
        )
    
    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.grants import grants_bp
    from app.routes.admin import admin_bp
    from app.routes.settings import settings_bp
    from app.routes.prices import prices_bp
    from app.routes.transactions import transactions_bp
    from app.routes.tax_center import tax_center_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(grants_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(prices_bp)
    app.register_blueprint(transactions_bp, url_prefix='/transactions')
    app.register_blueprint(tax_center_bp)

    
    # Register error handlers
    register_error_handlers(app)
    
    # Create database tables / migrate (never take down the process)
    with app.app_context():
        import logging
        _log = logging.getLogger(__name__)

        try:
            # Register models without rebinding local name `app`
            from app.models import market_price as _market_price  # noqa: F401
            from app.models import tax_profile as _tax_profile  # noqa: F401
            from app.models import advisor_job as _advisor_job  # noqa: F401
            db.create_all()
        except Exception as e:
            _log.exception('db.create_all failed (continuing): %s', e)

        def _safe_migrate(name, fn):
            try:
                fn(app)
            except Exception as e:
                _log.warning('%s migration failed (continuing): %s', name, e)

        from app.utils.migrate_transactions import migrate_transactions
        _safe_migrate('transactions', migrate_transactions)

        from app.utils.migrate_ss_wage_base import migrate_ss_wage_base
        _safe_migrate('ss_wage_base', migrate_ss_wage_base)

        from app.utils.migrate_tax_profile_state import migrate_tax_profile_state
        _safe_migrate('tax_profile_state', migrate_tax_profile_state)

        from app.utils.migrate_user_xai_key import migrate_user_xai_key
        _safe_migrate('user_xai_key', migrate_user_xai_key)

        from app.utils.migrate_advisor_jobs import migrate_advisor_jobs
        _safe_migrate('advisor_jobs', migrate_advisor_jobs)

        try:
            from app.utils.init_db import init_admin_user
            init_admin_user()
        except Exception as e:
            _log.warning('init_admin_user failed (continuing): %s', e)

        # Best-effort public market sync (SPCX); failures must not block boot
        try:
            from app.utils.market_data import sync_market_prices
            sync_market_prices(force=False)
        except Exception as e:
            _log.warning('Initial market price sync skipped: %s', e)

    return app



def _wants_json():
    """True for API/JSON clients so we never return HTML error pages to fetch()."""
    from flask import request
    path = request.path or ''
    if path.startswith('/tax/api') or path.startswith('/api/') or '/api/' in path:
        return True
    if request.is_json:
        return True
    # Accept header may be a list-like; check raw header too
    accept = (request.headers.get('Accept') or '') + ' ' + (request.accept_mimetypes.best or '')
    if 'application/json' in accept:
        return True
    if 'application/json' in (request.content_type or ''):
        return True
    return False


def register_error_handlers(app):
    """Register error handlers. API paths always get JSON — handlers must never raise."""
    from flask import render_template, jsonify, redirect, url_for, Response
    from flask_wtf.csrf import CSRFError
    import json as _json
    import traceback as _tb

    def _safe_audit(event, details=None):
        try:
            from app.utils.audit_log import AuditLogger
            AuditLogger.log_security_event(event, details or {})
        except Exception:
            pass

    def _json_err(payload, status=500):
        try:
            body = _json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:
            body = '{"success":false,"error":"error handler failed","code":"handler_fail"}'
        return Response(body, status=status, mimetype='application/json; charset=utf-8')

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        _safe_audit('CSRF_FAILURE', {'error': str(e)})
        if _wants_json():
            return _json_err({
                'success': False,
                'error': 'CSRF validation failed. Refresh the page and try again.',
                'code': 'csrf',
                'api_ok': False,
            }, 400)
        try:
            return render_template('errors/403.html'), 400
        except Exception:
            return 'Forbidden', 400

    @app.errorhandler(401)
    def unauthorized(e):
        if _wants_json():
            return _json_err({'success': False, 'error': 'Login required', 'code': 'auth'}, 401)
        return redirect(url_for('auth.login'))

    @app.errorhandler(403)
    def forbidden(e):
        _safe_audit('403_FORBIDDEN', {'error': str(e)})
        if _wants_json():
            return _json_err({'success': False, 'error': 'Forbidden', 'code': 'forbidden'}, 403)
        try:
            return render_template('errors/403.html'), 403
        except Exception:
            return 'Forbidden', 403

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return _json_err({'success': False, 'error': 'Not found', 'code': 'not_found'}, 404)
        try:
            return render_template('errors/404.html'), 404
        except Exception:
            return 'Not found', 404

    @app.errorhandler(500)
    def internal_error(e):
        _safe_audit('500_ERROR', {'error': str(e)})
        if _wants_json():
            return _json_err({
                'success': False,
                'error': str(e) or 'Internal server error',
                'code': 'server_error',
                'phase': 'flask_500_handler',
                'api_ok': False,
            }, 500)
        try:
            return render_template('errors/500.html'), 500
        except Exception:
            return 'Internal Server Error', 500

    # Catch-all: API always JSON. Never re-raise (re-raise → bare HTML 500).
    @app.errorhandler(Exception)
    def unhandled_exception(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            if _wants_json():
                return _json_err({
                    'success': False,
                    'error': getattr(e, 'description', None) or str(e),
                    'code': 'http_error',
                    'api_ok': False,
                }, int(e.code or 500))
            return e
        _safe_audit('UNHANDLED_EXCEPTION', {'error': str(e)})
        if _wants_json():
            return _json_err({
                'success': False,
                'error': str(e),
                'code': 'unhandled_exception',
                'phase': 'app_exception_handler',
                'api_ok': False,
                'detail': _tb.format_exc()[-1200:],
            }, 500)
        # HTML pages: use 500 template without re-raising
        try:
            return render_template('errors/500.html'), 500
        except Exception:
            return 'Internal Server Error', 500

    @app.errorhandler(429)
    def rate_limit_exceeded(e):
        AuditLogger.log_security_event('RATE_LIMIT_EXCEEDED', {'error': str(e)})
        if _wants_json():
            return jsonify({'error': 'Rate limit exceeded', 'code': 'rate_limit'}), 429
        return render_template('errors/429.html'), 429

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        if _wants_json():
            return jsonify({
                'error': 'Session expired or not logged in. Refresh and log in again.',
                'code': 'auth',
            }), 401
        return redirect(url_for('auth.login'))


@login_manager.user_loader
def load_user(user_id):
    """Load user by ID for Flask-Login."""
    from app.models.user import User
    return User.query.get(int(user_id))

