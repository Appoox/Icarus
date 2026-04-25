from django import forms
from django.contrib.auth import get_user_model
from .models import ReaderUser
from issue.models import Topic
from phonenumber_field.formfields import SplitPhoneNumberField

User = get_user_model()

class AllauthSignupForm(forms.Form):
    """
    Custom signup form for django-allauth to capture Reader-specific fields.
    """
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'placeholder': 'Full name',
            'class': 'form-input',
        }),
    )

    def signup(self, request, user):
        """
        Invoked by allauth at signup time.
        """
        user.name = self.cleaned_data.get('name', '')
        user.save()

class ReaderProfileEditForm(forms.ModelForm):
    """
    Allows a reader to update their profile details.
    """
    phone_number = SplitPhoneNumberField(
        region='IN',
        required=False,
    )
    care_of_number = SplitPhoneNumberField(
        region='IN',
        required=False,
    )

    class Meta:
        model = ReaderUser
        fields = (
            'name', 'email', 'phone_number', 'profile_image', 'bio', 
            'gender', 'gender_other', 'date_of_birth',
            'address_line_1', 'address_line_2', 'city', 'post_office', 'pincode', 'district', 'state',
            'care_of_name', 'care_of_number', 'care_of_district', 'care_of_meghala', 'care_of_unit'
        )
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Full name', 'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'placeholder': 'Email address', 'class': 'form-input'}),
            'bio': forms.Textarea(attrs={'placeholder': 'Tell us about yourself...', 'class': 'form-input', 'rows': 3}),
            'gender': forms.Select(attrs={'class': 'form-input'}),
            'gender_other': forms.TextInput(attrs={'placeholder': 'If other, please specify', 'class': 'form-input'}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'address_line_1': forms.TextInput(attrs={'placeholder': 'House / flat no., street', 'class': 'form-input'}),
            'address_line_2': forms.TextInput(attrs={'placeholder': 'Landmark, area, locality', 'class': 'form-input'}),
            'city': forms.TextInput(attrs={'placeholder': 'City / town', 'class': 'form-input'}),
            'state': forms.Select(attrs={'class': 'form-input'}),
            'pincode': forms.TextInput(attrs={'placeholder': '6-digit pincode', 'class': 'form-input', 'inputmode': 'numeric', 'maxlength': '6'}),
            'post_office': forms.TextInput(attrs={'placeholder': 'Post Office', 'class': 'form-input'}),
            'district': forms.TextInput(attrs={'placeholder': 'District', 'class': 'form-input'}),
            'care_of_name': forms.TextInput(attrs={'placeholder': 'Name of the person who added you', 'class': 'form-input'}),
            'care_of_district': forms.TextInput(attrs={'placeholder': 'District of the person who added you', 'class': 'form-input'}),
            'care_of_meghala': forms.TextInput(attrs={'placeholder': 'Meghala', 'class': 'form-input'}),
            'care_of_unit': forms.TextInput(attrs={'placeholder': 'Unit', 'class': 'form-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply styling to the split phone widgets
        for field_name in ['phone_number', 'care_of_number']:
            if field_name in self.fields:
                for widget in self.fields[field_name].widget.widgets:
                    widget.attrs.update({'class': 'form-input'})

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            if User.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('This email is already registered.')
        return email

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number')
        if phone_number:
            if User.objects.filter(phone_number=phone_number).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('This phone number is already registered.')
        return phone_number

    def clean_pincode(self):
        pincode = self.cleaned_data.get('pincode')
        if pincode:
            import re
            if not re.match(r'^[1-9][0-9]{5}$', pincode):
                raise forms.ValidationError('Enter a valid 6-digit Indian pincode.')
        return pincode


class UpdateInterestsForm(forms.ModelForm):
    interested_topics = forms.ModelMultipleChoiceField(
        queryset=Topic.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'topic-checkbox',
        }),
        label='Topics You\'re Interested In',
    )

    class Meta:
        model = ReaderUser
        fields = ('interested_topics',)