from allauth.account.adapter import DefaultAccountAdapter
from phonenumber_field.formfields import SplitPhoneNumberField
from django import forms
from reader.models import Reader

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
        if Reader.objects.filter(phone_number=phone).exists():
            raise forms.ValidationError('This phone number is already registered.')
        return phone

    def set_phone(self, user, phone, verified):
        """
        Save the phone number to the Reader profile.
        """
        phone_str = str(phone)
        reader, created = Reader.objects.get_or_create(
            user=user,
            defaults={'phone_number': phone_str, 'email': user.email}
        )
        if not created:
            reader.phone_number = phone_str
            reader.save()

    def get_user_by_phone(self, phone):
        """
        Look up a user by their phone number.
        """
        try:
            reader = Reader.objects.get(phone_number=phone)
            return reader.user
        except Reader.DoesNotExist:
            return None

    def get_phone(self, user):
        """
        Return the phone number for the given user as a tuple (phone, verified).
        """
        try:
            phone = user.reader.phone_number
            if phone:
                return (str(phone), False)
            return None
        except Reader.DoesNotExist:
            return None
