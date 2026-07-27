"""
Security and audit logging utilities.
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from flask import request, has_request_context
from flask_login import current_user
from functools import wraps


# Create logs directory if it doesn't exist (never crash import on read-only FS)
try:
    Path('logs').mkdir(exist_ok=True)
except Exception:
    pass

# Configure audit logger
audit_logger = logging.getLogger('audit')
audit_logger.setLevel(logging.INFO)
try:
    audit_handler = logging.FileHandler('logs/audit.log')
    audit_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    audit_handler.setFormatter(audit_formatter)
    audit_logger.addHandler(audit_handler)
except Exception:
    audit_logger.addHandler(logging.StreamHandler())

# Configure security logger
security_logger = logging.getLogger('security')
security_logger.setLevel(logging.WARNING)
try:
    security_handler = logging.FileHandler('logs/security.log')
    security_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    security_handler.setFormatter(security_formatter)
    security_logger.addHandler(security_handler)
except Exception:
    security_logger.addHandler(logging.StreamHandler())


class AuditLogger:
    """Centralized audit logging for security-sensitive operations."""
    
    @staticmethod
    def _get_user_context():
        """Get current user context for logging. Never raises."""
        try:
            if not has_request_context():
                return {}
            authed = False
            user_id = None
            username = 'anonymous'
            try:
                authed = bool(getattr(current_user, 'is_authenticated', False))
                if authed:
                    user_id = getattr(current_user, 'id', None)
                    username = getattr(current_user, 'username', None) or 'user'
            except Exception:
                pass
            return {
                'user_id': user_id,
                'username': username,
                'ip_address': getattr(request, 'remote_addr', None),
                'user_agent': (request.headers.get('User-Agent', 'Unknown') or 'Unknown')[:100],
            }
        except Exception:
            return {}
    
    @staticmethod
    def _format_log(event_type: str, details: dict):
        """Format audit log entry. Never raises."""
        try:
            context = AuditLogger._get_user_context()
            log_entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'event_type': event_type,
                **context,
                **(details or {}),
            }
            return json.dumps(log_entry, default=str)
        except Exception as e:
            return json.dumps({'event_type': event_type, 'format_error': str(e)})
    
    @staticmethod
    def log_auth_success(username: str):
        """Log successful authentication."""
        log_msg = AuditLogger._format_log('AUTH_SUCCESS', {'username': username})
        audit_logger.info(log_msg)
    
    @staticmethod
    def log_auth_failure(username: str, reason: str = 'invalid_credentials'):
        """Log failed authentication attempt."""
        log_msg = AuditLogger._format_log('AUTH_FAILURE', {
            'username': username,
            'reason': reason
        })
        security_logger.warning(log_msg)
    
    @staticmethod
    def log_logout(username: str):
        """Log user logout."""
        log_msg = AuditLogger._format_log('LOGOUT', {'username': username})
        audit_logger.info(log_msg)
    
    @staticmethod
    def log_password_change(user_id: int):
        """Log password change."""
        log_msg = AuditLogger._format_log('PASSWORD_CHANGE', {'user_id': user_id})
        audit_logger.info(log_msg)
    
    @staticmethod
    def log_account_creation(username: str, email: str):
        """Log new account creation."""
        log_msg = AuditLogger._format_log('ACCOUNT_CREATED', {
            'username': username,
            'email': email
        })
        audit_logger.info(log_msg)
    
    @staticmethod
    def log_grant_created(grant_id: int, grant_type: str, share_quantity: float):
        """Log grant creation."""
        log_msg = AuditLogger._format_log('GRANT_CREATED', {
            'grant_id': grant_id,
            'grant_type': grant_type,
            'share_quantity': share_quantity
        })
        audit_logger.info(log_msg)
    
    @staticmethod
    def log_grant_modified(grant_id: int, changes: dict):
        """Log grant modification."""
        log_msg = AuditLogger._format_log('GRANT_MODIFIED', {
            'grant_id': grant_id,
            'changes': changes
        })
        audit_logger.info(log_msg)
    
    @staticmethod
    def log_grant_deleted(grant_id: int, grant_type: str):
        """Log grant deletion."""
        log_msg = AuditLogger._format_log('GRANT_DELETED', {
            'grant_id': grant_id,
            'grant_type': grant_type
        })
        audit_logger.warning(log_msg)
    
    @staticmethod
    def log_vest_event_updated(event_id: int, vest_date: str):
        """Log vest event update."""
        log_msg = AuditLogger._format_log('VEST_EVENT_UPDATED', {
            'event_id': event_id,
            'vest_date': vest_date
        })
        audit_logger.info(log_msg)
    
    @staticmethod
    def log_tax_settings_changed(user_id: int, settings: dict):
        """Log tax settings modification."""
        log_msg = AuditLogger._format_log('TAX_SETTINGS_CHANGED', {
            'user_id': user_id,
            'settings': settings
        })
        audit_logger.info(log_msg)
    
    @staticmethod
    def log_admin_action(action: str, details: dict):
        """Log administrative action."""
        log_msg = AuditLogger._format_log('ADMIN_ACTION', {
            'action': action,
            **details
        })
        audit_logger.warning(log_msg)
    
    @staticmethod
    def log_unauthorized_access(resource: str, required_permission: str = None):
        """Log unauthorized access attempt."""
        log_msg = AuditLogger._format_log('UNAUTHORIZED_ACCESS', {
            'resource': resource,
            'required_permission': required_permission
        })
        security_logger.error(log_msg)
    
    @staticmethod
    def log_suspicious_activity(activity_type: str, details: dict):
        """Log suspicious activity."""
        log_msg = AuditLogger._format_log('SUSPICIOUS_ACTIVITY', {
            'activity_type': activity_type,
            **details
        })
        security_logger.critical(log_msg)
    
    @staticmethod
    def log_rate_limit_exceeded(endpoint: str):
        """Log rate limit violation."""
        log_msg = AuditLogger._format_log('RATE_LIMIT_EXCEEDED', {
            'endpoint': endpoint
        })
        security_logger.warning(log_msg)
    
    @staticmethod
    def log_csrf_failure():
        """Log CSRF token validation failure."""
        log_msg = AuditLogger._format_log('CSRF_FAILURE', {})
        security_logger.error(log_msg)
    
    @staticmethod
    def log_input_validation_failure(field: str, value_type: str):
        """Log input validation failure."""
        log_msg = AuditLogger._format_log('INPUT_VALIDATION_FAILURE', {
            'field': field,
            'value_type': value_type
        })
        security_logger.warning(log_msg)
    
    @staticmethod
    def log_security_event(event_type: str, details: dict):
        """
        Log a generic security event. Never raises (must not crash error handlers).
        """
        try:
            log_msg = AuditLogger._format_log(event_type, details or {})
            security_logger.warning(log_msg)
        except Exception:
            pass


def audit_log(event_type: str):
    """
    Decorator for audit logging function calls.
    
    Usage:
        @audit_log('GRANT_CREATED')
        def create_grant(...):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
                log_msg = AuditLogger._format_log(event_type, {
                    'function': func.__name__,
                    'success': True
                })
                audit_logger.info(log_msg)
                return result
            except Exception as e:
                log_msg = AuditLogger._format_log(event_type, {
                    'function': func.__name__,
                    'success': False,
                    'error': str(e)
                })
                security_logger.error(log_msg)
                raise
        return wrapper
    return decorator
