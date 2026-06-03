import logging
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

class PhoneNumberBackend(ModelBackend):
    """
    Custom authentication backend that authenticates users using their phone number
    (hashed with HMAC-SHA256 to match the phone_number_hash in the database).
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get('username') or kwargs.get('phone')

        if not username:
            return None

        UserModel = get_user_model()
        
        # Clean and normalize the username/phone number input
        username_str = str(username).strip()
        
        # Try to normalize a bare 10-digit number to E.164 (+91 prefix for India)
        normalized = username_str
        if normalized.isdigit() and len(normalized) == 10:
            normalized = f"+91{normalized}"

        try:
            # Hash the normalized phone number
            hashed = UserModel.hash_phone(normalized)
            user = UserModel.objects.filter(phone_number_hash=hashed).first()
            
            # Check the password and active status
            if user and user.check_password(password):
                if self.user_can_authenticate(user):
                    return user
        except Exception as e:
            logger.error(f"Error in PhoneNumberBackend authentication: {e}")
            return None
            
        return None
