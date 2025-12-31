"""
Password security and validation utilities.
"""

import re
from typing import Tuple, List


class PasswordValidator:
    """Validates password strength and security requirements."""
    
    # Common passwords to reject (subset - full list should be much larger)
    COMMON_PASSWORDS = {
        'password', 'password123', '123456', '12345678', 'qwerty',
        'abc123', 'monkey', '1234567', 'letmein', 'trustno1',
        'dragon', 'baseball', 'iloveyou', 'master', 'sunshine',
        'ashley', 'bailey', 'passw0rd', 'shadow', 'superman',
        'qazwsx', '123456789', 'password1', 'admin', 'admin123'
    }
    
    def __init__(
        self,
        min_length: int = 12,
        max_length: int = 128,
        require_uppercase: bool = True,
        require_lowercase: bool = True,
        require_digit: bool = True,
        require_special: bool = True
    ):
        self.min_length = min_length
        self.max_length = max_length
        self.require_uppercase = require_uppercase
        self.require_lowercase = require_lowercase
        self.require_digit = require_digit
        self.require_special = require_special
    
    def validate(self, password: str, username: str = None) -> Tuple[bool, List[str]]:
        """
        Validate password against security requirements.
        
        Args:
            password: The password to validate
            username: Optional username to check for similarity
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        # Length checks
        if len(password) < self.min_length:
            errors.append(f'Password must be at least {self.min_length} characters long')
        
        if len(password) > self.max_length:
            errors.append(f'Password must not exceed {self.max_length} characters')
        
        # Complexity checks
        if self.require_uppercase and not re.search(r'[A-Z]', password):
            errors.append('Password must contain at least one uppercase letter')
        
        if self.require_lowercase and not re.search(r'[a-z]', password):
            errors.append('Password must contain at least one lowercase letter')
        
        if self.require_digit and not re.search(r'\d', password):
            errors.append('Password must contain at least one digit')
        
        if self.require_special and not re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'",.<>?/\\|`~]', password):
            errors.append('Password must contain at least one special character (!@#$%^&*()_+-=[]{}...)')
        
        # Common password check
        if password.lower() in self.COMMON_PASSWORDS:
            errors.append('This password is too common. Please choose a more unique password')
        
        # Username similarity check
        if username and username.lower() in password.lower():
            errors.append('Password must not contain your username')
        
        # Sequential characters check
        if self._has_sequential_chars(password):
            errors.append('Password must not contain sequential characters (e.g., 123, abc)')
        
        # Repeated characters check
        if self._has_repeated_chars(password):
            errors.append('Password must not contain repeated characters (e.g., aaa, 111)')
        
        return (len(errors) == 0, errors)
    
    def _has_sequential_chars(self, password: str, length: int = 3) -> bool:
        """Check for sequential characters."""
        for i in range(len(password) - length + 1):
            substr = password[i:i+length].lower()
            # Check for sequential numbers
            if substr.isdigit():
                nums = [int(c) for c in substr]
                if all(nums[j] + 1 == nums[j+1] for j in range(len(nums)-1)):
                    return True
                if all(nums[j] - 1 == nums[j+1] for j in range(len(nums)-1)):
                    return True
            # Check for sequential letters
            if substr.isalpha():
                chars = [ord(c) for c in substr]
                if all(chars[j] + 1 == chars[j+1] for j in range(len(chars)-1)):
                    return True
                if all(chars[j] - 1 == chars[j+1] for j in range(len(chars)-1)):
                    return True
        return False
    
    def _has_repeated_chars(self, password: str, length: int = 3) -> bool:
        """Check for repeated characters."""
        for i in range(len(password) - length + 1):
            if len(set(password[i:i+length])) == 1:
                return True
        return False
    
    def get_strength_score(self, password: str) -> int:
        """
        Calculate password strength score (0-100).
        
        Returns:
            Integer score from 0 (very weak) to 100 (very strong)
        """
        score = 0
        
        # Length contribution (max 30 points)
        length_score = min(30, (len(password) - 8) * 2)
        score += length_score
        
        # Character variety (max 40 points)
        has_lower = bool(re.search(r'[a-z]', password))
        has_upper = bool(re.search(r'[A-Z]', password))
        has_digit = bool(re.search(r'\d', password))
        has_special = bool(re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'",.<>?/\\|`~]', password))
        
        variety_score = (has_lower + has_upper + has_digit + has_special) * 10
        score += variety_score
        
        # Unique characters (max 20 points)
        unique_ratio = len(set(password)) / len(password) if password else 0
        score += int(unique_ratio * 20)
        
        # Entropy bonus (max 10 points)
        if len(password) > 15:
            score += 5
        if len(set(password)) > 10:
            score += 5
        
        # Penalties
        if password.lower() in self.COMMON_PASSWORDS:
            score -= 50
        if self._has_sequential_chars(password):
            score -= 20
        if self._has_repeated_chars(password):
            score -= 15
        
        return max(0, min(100, score))
    
    def get_strength_text(self, password: str) -> str:
        """Get human-readable password strength."""
        score = self.get_strength_score(password)
        
        if score < 20:
            return 'Very Weak'
        elif score < 40:
            return 'Weak'
        elif score < 60:
            return 'Fair'
        elif score < 80:
            return 'Strong'
        else:
            return 'Very Strong'


# Global validator instance
password_validator = PasswordValidator(
    min_length=1,  # Only require at least 1 character
    max_length=128,
    require_uppercase=False,
    require_lowercase=False,
    require_digit=False,
    require_special=False
)


def validate_password(password: str, username: str = None) -> Tuple[bool, List[str]]:
    """Convenience function for password validation."""
    return password_validator.validate(password, username)


def get_password_strength(password: str) -> dict:
    """Get password strength information."""
    return {
        'score': password_validator.get_strength_score(password),
        'text': password_validator.get_strength_text(password),
        'is_strong': password_validator.get_strength_score(password) >= 60
    }
