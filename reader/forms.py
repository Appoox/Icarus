# reader/forms.py

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
# 'timezone' and 'date' removed — not used anywhere in this module

from .models import ReaderUser
from issue.models import Topic
from phonenumber_field.formfields import SplitPhoneNumberField

# Wagtail user forms imports
from wagtail.users.forms import UserEditForm, UserCreationForm

User = get_user_model()

# NOTE: AllauthSignupForm fields have been migrated directly to CustomSignupForm 
# inside `auth_forms.py` to prevent circular lookup issues during account creation.


class ReaderProfileEditForm(forms.ModelForm):
    """
    Form for readers to update their personal details, subscription delivery settings,
    and mailing address options. Includes complete tracking for local magazine variations.
    """
    phone_number = SplitPhoneNumberField(
        label=_("Phone Number"),
        required=True
    )

    class Meta:
        model = ReaderUser
        fields = [
            'name', 'email', 'phone_number', 'gender', 'birth_year',  # was 'dob' (renamed field)
            'profile_image', 'bio',
            # 'magazine_format' removed — field no longer exists on ReaderUser
            'address_line_1', 'address_line_2', 'post_office',
            'city', 'pincode', 'district', 'state', 'delivery_notes',
            'care_of_name', 'care_of_number', 'care_of_district',
            'care_of_meghala', 'care_of_unit'
        ]
        widgets = {
            'birth_year': forms.NumberInput(attrs={'class': 'form-input', 'min': 1900, 'max': 2099}),  # was 'dob'
            'bio': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            # 'magazine_format' widget removed — field no longer exists on ReaderUser
            'gender': forms.Select(attrs={'class': 'form-input'}),
            'delivery_notes': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Eg: Leave at security desk', 'class': 'form-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Explicit layout configurations for profile field sets
        text_fields = [
            'name', 'email', 'address_line_1', 'address_line_2', 
            'post_office', 'city', 'pincode', 'district', 'state',
            'care_of_name', 'care_of_number', 'care_of_district',
            'care_of_meghala', 'care_of_unit'
        ]
        for field_name in text_fields:
            if field_name in self.fields:
                existing_class = self.fields[field_name].widget.attrs.get('class', '')
                self.fields[field_name].widget.attrs['class'] = f"{existing_class} form-input".strip()

        # Setup phone sub-widgets correctly
        if 'phone_number' in self.fields:
            field = self.fields['phone_number']
            if hasattr(field.widget, 'widgets'):
                for widget in field.widget.widgets:
                    existing_class = widget.attrs.get('class', '')
                    widget.attrs['class'] = f"{existing_class} form-input".strip()
                if len(field.widget.widgets) > 1:
                    field.widget.widgets[1].attrs.update({
                        'inputmode': 'numeric',
                        'pattern': '[0-9]*',
                        'oninput': "this.value = this.value.replace(/[^0-9]/g, '');"
                    })

        # Initialize field instance values cleanly
        if self.instance and self.instance.pk:
            decrypted_phone = getattr(self.instance, 'phone_number_encrypted', None)
            if decrypted_phone:
                self.fields['phone_number'].initial = str(decrypted_phone)


class UpdateInterestsForm(forms.ModelForm):
    """
    Form handling the customization of reader topics and interest classifications.
    """
    # Field name must match the model's M2M field name exactly so ModelForm maps it correctly.
    interested_topics = forms.ModelMultipleChoiceField(  # was ModelMultipleChoiceForm (typo) + wrong name 'interests'
        queryset=Topic.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label=_("Select your topics of interest")
    )

    class Meta:
        model = ReaderUser
        fields = ['interested_topics']  # was ['interests'] — FieldError at form init


class CustomWagtailUserCreationForm(UserCreationForm):
    """
    Custom implementation of Wagtail's User Creation form matching structural custom user needs.
    """
    name = forms.CharField(max_length=255, required=True, label=_("Full Name"))

    class Meta:
        model = User
        fields = {'phone_number_hash', 'name', 'email', 'is_active'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if 'phone_number_hash' in self.fields:
            self.fields['phone_number_hash'].widget = forms.HiddenInput()
            self.fields['phone_number_hash'].required = False
            
        if 'email' in self.fields:
            self.fields['email'].required = False
            self.fields['email'].empty_value = None

    def validate_unique(self):
        """
        Catch the email before uniqueness validation.
        """
        if self.instance.email == "":
            self.instance.email = None
        super().validate_unique()


class CustomWagtailUserEditForm(UserEditForm):
    """
    Custom implementation of Wagtail's User Editing form to safely bridge 
    properties, optional values, and dynamic multi-part names.
    """
    name = forms.CharField(max_length=255, required=True, label=_("Full Name"))

    class Meta:
        model = User
        fields = {
            'phone_number_hash', 
            'name', 'email', 'is_active', 'gender', 'birth_year',
            'subscription_plan', 'subscription_end',
            'print_delivery_status',
            'pincode'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if 'phone_number_hash' in self.fields:
            self.fields['phone_number_hash'].widget = forms.HiddenInput()
            self.fields['phone_number_hash'].disabled = True
            self.fields['phone_number_hash'].required = False
            
        if 'email' in self.fields:
            self.fields['email'].required = False
            self.fields['email'].empty_value = None  # Force empty to None early

        for field_name in ('first_name', 'last_name'):
            if field_name in self.fields:
                self.fields[field_name].required = False
                self.fields[field_name].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        full_name = cleaned_data.get('name', '').strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            if len(parts) == 1:
                cleaned_data['first_name'] = parts[0]
                cleaned_data['last_name'] = ""
            else:
                cleaned_data['first_name'] = parts[0]
                cleaned_data['last_name'] = parts[1]
        else:
            cleaned_data['first_name'] = ""
            cleaned_data['last_name'] = ""
        return cleaned_data

    def validate_unique(self):
        """
        Crucial fix for Django's AbstractUser:
        AbstractUser.clean() normalizes email addresses and automatically mutates 
        `None` back into `""` (empty string). This triggers a unique constraint 
        violation. We force it back to `None` right before the uniqueness check runs.
        """
        if self.instance.email == "":
            self.instance.email = None
        super().validate_unique()
