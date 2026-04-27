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
        Extend user saving to capture registration metadata.
        """
        user = super().save_user(request, user, form, commit=False)
        
        # Capture registration metadata
        user.registration_ip = self.get_client_ip(request)
        user.accepted_terms_at = timezone.now()
        user.terms_version = ReaderUser.CURRENT_TERMS_VERSION
        user.accepted_privacy_at = timezone.now()
        user.privacy_version = ReaderUser.CURRENT_PRIVACY_VERSION
        
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
        Validate the phone number and check for uniqueness.
        """
        phone = super().clean_phone(phone)
        if ReaderUser.objects.filter(phone_number=phone).exists():
            raise forms.ValidationError('This phone number is already registered.')
        return phone

    def set_phone(self, user, phone, verified):
        """
        Save the phone number directly to the User.
        """
        user.phone_number = str(phone)
        user.save()

    def get_user_by_phone(self, phone):
        """
        Look up a user by their phone number.
        """
        try:
            return ReaderUser.objects.get(phone_number=phone)
        except ReaderUser.DoesNotExist:
            return None

    def get_phone(self, user):
        """
        Return the phone number for the given user.
        """
        phone = getattr(user, 'phone_number', None)
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
