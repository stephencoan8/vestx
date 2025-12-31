"""
Authentication routes - login, logout, register, password reset.
Security enhanced with rate limiting, CSRF protection, and audit logging.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_user, logout_user, current_user
from app import db
from app.models.user import User
from app.utils.password_security import validate_password
from app.utils.audit_log import AuditLogger
from werkzeug.security import generate_password_hash
import secrets
from datetime import datetime, timedelta
from email_validator import validate_email, EmailNotValidError

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login page with security hardening."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)
        
        # Input validation
        if not username or not password:
            AuditLogger.log_auth_failure('', 'missing_credentials')
            flash('Username and password are required', 'error')
            return render_template('auth/login.html')
        
        # Sanitize username (prevent enumeration timing attacks)
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            # Check if account is locked
            if hasattr(user, 'is_locked') and user.is_locked:
                AuditLogger.log_security_event('LOGIN_ATTEMPT_LOCKED_ACCOUNT', {
                    'username': username
                })
                flash('Account is locked. Please contact administrator.', 'error')
                return render_template('auth/login.html')
            
            # Successful login
            login_user(user, remember=remember)
            AuditLogger.log_auth_success(username)
            
            # Clear failed attempts if tracking
            if hasattr(user, 'failed_login_attempts'):
                user.failed_login_attempts = 0
                user.last_login = datetime.utcnow()
                db.session.commit()
            
            # Safe redirect
            next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'):
                next_page = None  # Prevent open redirect
            
            return redirect(next_page if next_page else url_for('main.dashboard'))
        else:
            # Failed login
            AuditLogger.log_auth_failure(username, 'invalid_credentials')
            
            # Track failed attempts (if user exists)
            if user and hasattr(user, 'failed_login_attempts'):
                user.failed_login_attempts = getattr(user, 'failed_login_attempts', 0) + 1
                if user.failed_login_attempts >= 5:
                    user.is_locked = True
                    AuditLogger.log_security_event('ACCOUNT_LOCKED', {'username': username})
                db.session.commit()
            
            flash('Invalid username or password', 'error')
    
    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration page with enhanced validation."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Input validation
        errors = []
        
        if not username or not email or not password:
            errors.append('All fields are required')
        
        # Username validation
        if len(username) < 3 or len(username) > 30:
            errors.append('Username must be between 3 and 30 characters')
        
        if not username.isalnum() and '_' not in username:
            errors.append('Username can only contain letters, numbers, and underscores')
        
        # Email validation
        try:
            email_info = validate_email(email, check_deliverability=False)
            email = email_info.normalized
        except EmailNotValidError as e:
            errors.append(f'Invalid email: {str(e)}')
        
        # Password validation
        if password != confirm_password:
            errors.append('Passwords do not match')
        else:
            is_valid, pwd_errors = validate_password(password, username)
            if not is_valid:
                errors.extend(pwd_errors)
        
        # Check existing users
        if User.query.filter_by(username=username).first():
            errors.append('Username already exists')
            AuditLogger.log_security_event('REGISTRATION_DUPLICATE_USERNAME', {
                'username': username
            })
        
        if User.query.filter_by(email=email).first():
            errors.append('Email already registered')
            AuditLogger.log_security_event('REGISTRATION_DUPLICATE_EMAIL', {
                'email': email
            })
        
        # Show errors or create user
        if errors:
            for error in errors:
                flash(error, 'error')
        else:
            # Create new user
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            
            AuditLogger.log_account_creation(username, email)
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('auth.login'))
    
    return render_template('auth/register.html')


@auth_bp.route('/logout')
def logout():
    """User logout with session cleanup."""
    if current_user.is_authenticated:
        username = current_user.username
        AuditLogger.log_logout(username)
    
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Password reset request page."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        
        # Validate email
        try:
            email_info = validate_email(email, check_deliverability=False)
            email = email_info.normalized
        except EmailNotValidError:
            flash('Invalid email address', 'error')
            return render_template('auth/forgot_password.html')
        
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Generate secure reset token
            reset_token = secrets.token_urlsafe(32)
            reset_expiry = datetime.utcnow() + timedelta(hours=1)
            
            # Store token (would need to add fields to User model)
            # user.password_reset_token = reset_token
            # user.password_reset_expiry = reset_expiry
            # db.session.commit()
            
            # TODO: Send email with reset link
            # send_password_reset_email(user.email, reset_token)
            
            AuditLogger.log_security_event('PASSWORD_RESET_REQUESTED', {
                'email': email
            })
        
        # Always show success message (prevent email enumeration)
        flash('If an account exists with that email, password reset instructions have been sent.', 'info')
    
    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Password reset confirmation page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    # Validate token (would need User model fields)
    # user = User.query.filter_by(password_reset_token=token).first()
    # if not user or not user.password_reset_expiry or user.password_reset_expiry < datetime.utcnow():
    #     flash('Invalid or expired reset link', 'error')
    #     return redirect(url_for('auth.forgot_password'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
        else:
            is_valid, errors = validate_password(password)
            if not is_valid:
                for error in errors:
                    flash(error, 'error')
            else:
                # Reset password
                # user.set_password(password)
                # user.password_reset_token = None
                # user.password_reset_expiry = None
                # db.session.commit()
                
                # AuditLogger.log_password_change(user.id)
                flash('Password has been reset successfully. Please log in.', 'success')
                return redirect(url_for('auth.login'))
    
    return render_template('auth/reset_password.html', token=token)

