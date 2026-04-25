from allauth.account.adapter import DefaultAccountAdapter
from phonenumber_field.formfields import SplitPhoneNumberField
from django import forms
from reader.models import ReaderUser

class CustomAccountAdapter(DefaultAccountAdapter):
    def phone_form_field(self, **kwargs):
        """
        Use SplitPhoneNumberField to provide a dropdown for the country code
        and a text input for the national number.
        """
        kwargs.setdefault('required', True)
        kwargs.setdefault('region', 'IN')
        return SplitPhoneNumberField(**kwargs)

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
        # user.save() will be called by allauth later or we can do it here
        # Actually set_phone is called before saving the user in some flows.
        # But for custom models we should ensure it's saved.

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
            return (str(phone), False)
        return None
