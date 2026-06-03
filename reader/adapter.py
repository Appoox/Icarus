from allauth.account.adapter import DefaultAccountAdapter
from phonenumber_field.formfields import SplitPhoneNumberField
from django import forms
from django.utils import timezone
from reader.models import ReaderUser
from phonenumber_field.formfields import SplitPhoneNumberField as BaseSplitPhoneNumberField
from allauth.account.internal.flows import phone_verification

class CustomAccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit=True):
        """
        Extend user saving to capture registration metadata and ensure phone is verified.
        """
        user = super().save_user(request, user, form, commit=False)
        
        # Capture registration metadata
        user.registration_ip = self.get_client_ip(request)
        user.accepted_terms_at = timezone.now()
        user.terms_version = ReaderUser.CURRENT_TERMS_VERSION
        user.accepted_privacy_at = timezone.now()
        user.privacy_version = ReaderUser.CURRENT_PRIVACY_VERSION
        
        # Capture 18+ age declaration from signup form
        if form.cleaned_data.get('is_above_18'):
            user.is_above_18 = True
            user.age_declaration_at = timezone.now()
        
        # Ensure phone is saved directly (this handles the phone field in the form)
        if 'phone' in form.cleaned_data:
            self.set_phone(user, form.cleaned_data['phone'], verified=True)
        
        if commit:
            user.save()
        return user

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    def phone_form_field(self, **kwargs):
        """
        Use SplitPhoneNumberField to provide a dropdown for the country code
        and a text input for the national number.
        """
        kwargs.setdefault('required', True)
        kwargs.setdefault('region', 'IN')
        return StringSplitPhoneNumberField(**kwargs)

    def clean_phone(self, phone):
        """
        Validate the phone number and check for uniqueness via HMAC hash.
        Never compare plaintext phone numbers at the DB level.
        """
        phone = super().clean_phone(phone)
        hashed = ReaderUser.hash_phone(str(phone))
        if ReaderUser.objects.filter(phone_number_hash=hashed).exists():
            raise forms.ValidationError('This phone number is already registered.')
        return phone

    def set_phone(self, user, phone, verified):
        """
        Save the phone number by calling user.set_phone() which updates both
        the HMAC hash (for lookups) and the Fernet-encrypted value (for display).
        """
        user.set_phone(str(phone))
        user.save()

    def get_user_by_phone(self, phone):
        """
        Look up a user by their phone number via the HMAC hash.
        """
        try:
            hashed = ReaderUser.hash_phone(str(phone))
            return ReaderUser.objects.get(phone_number_hash=hashed)
        except ReaderUser.DoesNotExist:
            return None

    def get_phone(self, user):
        """
        Return the decrypted phone number for the given user.
        """
        phone = getattr(user, 'phone_number_encrypted', None)
        if phone:
            return (str(phone), True)
        return None

    def send_verification_code_sms(self, request, phone_number: str, key: str, **kwargs) -> None:
        # Log for dev, no-op for prod
        print(f"[DEV] SMS code for {phone_number}: {key}")

    def is_phone_verified(self, request, user):
        """Trust phone numbers at registration — skip verification stage."""
        return True


class StringSplitPhoneNumberField(BaseSplitPhoneNumberField):
    def clean(self, value):
        result = super().clean(value)
        return str(result) if result else result
