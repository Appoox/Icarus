import re

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import Reader
from issue.models import Topic


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
        reader, created = Reader.objects.get_or_create(
            user=user,
            defaults={
                'name': self.cleaned_data.get('name', ''),
                'email': user.email,
            }
        )
        if not created:
            # If the reader was somehow created already, update it
            reader.name = self.cleaned_data.get('name', '')
            reader.save()




class ReaderSignupForm(UserCreationForm):
    """
    Extends Django's UserCreationForm with Reader-specific fields.
    Creates both the User and the linked Reader profile.
    """
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'placeholder': 'Full name',
            'class': 'form-input',
        }),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'placeholder': 'Email address',
            'class': 'form-input',
        }),
    )
    phone_number = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Phone number',
            'class': 'form-input',
        }),
    )
    interested_topics = forms.ModelMultipleChoiceField(
        queryset=Topic.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'topic-checkbox',
        }),
        help_text='Select topics you are interested in.',
    )

    # ── Address fields ───────────────────────────────────────────────
    address_line_1 = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'House / flat no., street',
            'class': 'form-input',
        }),
    )
    address_line_2 = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Landmark, area, locality',
            'class': 'form-input',
        }),
    )
    city = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'City / town',
            'class': 'form-input',
        }),
    )
    state = forms.ChoiceField(
        choices=Reader.INDIAN_STATES,
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-input',
        }),
    )
    pincode = forms.CharField(
        max_length=6,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': '6-digit pincode',
            'class': 'form-input',
            'inputmode': 'numeric',
            'maxlength': '6',
        }),
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')
        widgets = {
            'username': forms.TextInput(attrs={
                'placeholder': 'Username',
                'class': 'form-input',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({
            'placeholder': 'Password',
            'class': 'form-input',
        })
        self.fields['password2'].widget.attrs.update({
            'placeholder': 'Confirm password',
            'class': 'form-input',
        })

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('This email is already registered.')
        if Reader.objects.filter(email=email).exists():
            raise forms.ValidationError('This email is already registered.')
        return email

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number')
        if phone_number:
            if Reader.objects.filter(phone_number=phone_number).exists():
                raise forms.ValidationError('This phone number is already registered.')
        return phone_number

    def clean_pincode(self):
        pincode = self.cleaned_data.get('pincode')
        if pincode:
            if not re.match(r'^[1-9][0-9]{5}$', pincode):
                raise forms.ValidationError('Enter a valid 6-digit Indian pincode.')
        return pincode

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
            reader = Reader.objects.create(
                user=user,
                name=self.cleaned_data['name'],
                email=self.cleaned_data['email'],
                phone_number=self.cleaned_data.get('phone_number', ''),
                address_line_1=self.cleaned_data.get('address_line_1', ''),
                address_line_2=self.cleaned_data.get('address_line_2', ''),
                city=self.cleaned_data.get('city', ''),
                state=self.cleaned_data.get('state', ''),
                pincode=self.cleaned_data.get('pincode', ''),
            )
            topics = self.cleaned_data.get('interested_topics')
            if topics:
                reader.interested_topics.set(topics)
        return user


class ReaderProfileEditForm(forms.ModelForm):
    """
    Allows a reader to update their name, email, and phone number.
    Email and phone number uniqueness are checked against other records.
    """
    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'placeholder': 'Full name',
            'class': 'form-input',
        }),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'placeholder': 'Email address',
            'class': 'form-input',
        }),
    )
    phone_number = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Phone number',
            'class': 'form-input',
        }),
    )
    gender = forms.ChoiceField(
        choices=Reader.GENDER_CHOICES,
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-input',
        }),
    )
    gender_other = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'If other, please specify',
            'class': 'form-input',
        }),
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-input',
        }),
    )

    # ── Address fields ───────────────────────────────────────────────
    address_line_1 = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'House / flat no., street',
            'class': 'form-input',
        }),
    )
    address_line_2 = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Landmark, area, locality',
            'class': 'form-input',
        }),
    )
    city = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'City / town',
            'class': 'form-input',
        }),
    )
    state = forms.ChoiceField(
        choices=Reader.INDIAN_STATES,
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-input',
        }),
    )
    pincode = forms.CharField(
        max_length=6,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': '6-digit pincode',
            'class': 'form-input',
            'inputmode': 'numeric',
            'maxlength': '6',
        }),
    )
    post_office = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Post Office',
            'class': 'form-input',
        }),
    )
    district = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'District',
            'class': 'form-input',
        }),
    )

    # ── Added By fields ─────────────────────────────────────────────
    care_of_name = forms.CharField(
        required=False,
        label="Added By (Name)",
        widget=forms.TextInput(attrs={
            'placeholder': 'Name of the person who added you',
            'class': 'form-input',
        }),
    )
    care_of_number = forms.CharField(
        required=False,
        label="Added By (Phone)",
        widget=forms.TextInput(attrs={
            'placeholder': 'Phone number of the person who added you',
            'class': 'form-input',
        }),
    )
    care_of_district = forms.CharField(
        required=False,
        label="Added By (District)",
        widget=forms.TextInput(attrs={
            'placeholder': 'District of the person who added you',
            'class': 'form-input',
        }),
    )
    care_of_meghala = forms.CharField(
        required=False,
        label="Added By (Meghala)",
        widget=forms.TextInput(attrs={
            'placeholder': 'Meghala',
            'class': 'form-input',
        }),
    )
    care_of_unit = forms.CharField(
        required=False,
        label="Added By (Unit)",
        widget=forms.TextInput(attrs={
            'placeholder': 'Unit',
            'class': 'form-input',
        }),
    )

    class Meta:
        model = Reader
        fields = (
            'name', 'email', 'phone_number', 'gender', 'gender_other', 'date_of_birth',
            'address_line_1', 'address_line_2', 'city', 'post_office', 'pincode', 'district', 'state',
            'care_of_name', 'care_of_number', 'care_of_district', 'care_of_meghala', 'care_of_unit'
        )

    def clean_email(self):
        email = self.cleaned_data.get('email')
        # Exclude the current reader's own records from the uniqueness check
        if User.objects.filter(email=email).exclude(pk=self.instance.user_id).exists():
            raise forms.ValidationError('This email is already registered.')
        if Reader.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('This email is already registered.')
        return email

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number')
        if phone_number:
            if Reader.objects.filter(phone_number=phone_number).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError('This phone number is already registered.')
        return phone_number

    def clean_pincode(self):
        pincode = self.cleaned_data.get('pincode')
        if pincode:
            if not re.match(r'^[1-9][0-9]{5}$', pincode):
                raise forms.ValidationError('Enter a valid 6-digit Indian pincode.')
        return pincode

    def save(self, commit=True):
        reader = super().save(commit=False)
        if commit:
            reader.save()
            # Keep the linked User's email in sync
            user = reader.user
            user.email = self.cleaned_data['email']
            user.save(update_fields=['email'])
        return reader


# ✅ NEW: Lets existing readers update their interests from the profile page
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
        model = Reader
        fields = ('interested_topics',)