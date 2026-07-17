# reader/forms.py

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.utils import timezone  # needed for consent timestamp writes in save()
from datetime import datetime, time

from .models import ReaderUser
from issue.models import Topic
from phonenumber_field.formfields import SplitPhoneNumberField
from phonenumber_field.phonenumber import PhoneNumber as PhoneNumberObj  # for safe parse in __init__

from wagtail.users.forms import UserEditForm, UserCreationForm

User = get_user_model()

# NOTE: AllauthSignupForm fields have been migrated directly to CustomSignupForm 
# inside `auth_forms.py` to prevent circular lookup issues during account creation.


class ReaderProfileEditForm(forms.ModelForm):
    """
    Form for readers to update their personal details, delivery address, and
    data-sharing consent preferences.

    Consent fields (read_history_consent, gender_consent, birth_year_consent)
    are boolean form fields — NOT model fields.  They map to the model's *_at
    timestamp columns:
        checked   (True)  →  *_at = timezone.now()  (consent active)
        unchecked (False) →  *_at = None             (consent revoked)
    Translation happens in save(); initial state is derived in __init__.
    """
    phone_number = SplitPhoneNumberField(
        label=_("ഫോൺ നമ്പർ"),
        required=True,
        # region='IN' propagates to SplitPhoneNumberWidget(region='IN').
        # The widget stores it as self.region, which is passed to
        # PhoneNumber.from_string(value, region=self.region) inside decompress().
        # Without this, national-format numbers ("9876543210") cannot be parsed
        # at decompress() time because there is no country hint — the call returns
        # [None, None] and the field renders completely blank.
        region='IN',
    )

    # ── Consent toggle fields ────────────────────────────────────────────────
    # These are declared form fields (not model fields).  Django's construct_instance()
    # skips non-model fields during save, so the translation to *_at timestamps is
    # handled entirely by our custom save() below.
    read_history_consent = forms.BooleanField(
        required=False,
        label=_("വ്യക്തിഗത ശുപാർശകൾക്കായി എന്റെ വായനാ ചരിത്രം ട്രാക്ക് ചെയ്യുക. (Optional)"),
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        # Shown below the checkbox in the template via form.read_history_consent.help_text
        help_text=_("ഈ സമ്മതം പിൻവലിച്ചാൽ സൂക്ഷിച്ച നിങ്ങളുടെ വായനാ ചരിത്രം ശാശ്വതമായി ഇല്ലാതാക്കപ്പെടും."),
    )
    gender_consent = forms.BooleanField(
        required=False,
        label=_("വിശകലനത്തിൽ എന്റെ ലിംഗഭേദം ഉൾപ്പെടുത്തുക."),
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    birth_year_consent = forms.BooleanField(
        required=False,
        label=_("വിശകലനത്തിൽ എന്റെ ജനന വർഷം ഉൾപ്പെടുത്തുക."),
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )

    # ── Avatar upload fields ────────────────────────────────────────────────
    # Non-model form fields. profile_image is a wagtailimages.Image FK, so a
    # plain ModelForm can't accept a file for it — save() below wraps the
    # uploaded file in a Wagtail Image and points the FK at it. profile_image
    # is intentionally NOT in Meta.fields: leaving it there (unrendered) made
    # every profile save silently NULL the FK via construct_instance().
    avatar_upload = forms.ImageField(
        required=False,
        label=_("പ്രൊഫൈൽ ചിത്രം"),
        widget=forms.ClearableFileInput(attrs={'class': 'form-input', 'accept': 'image/*'}),
        help_text=_("JPG/PNG/WebP, പരമാവധി 5MB."),
    )
    remove_avatar = forms.BooleanField(
        required=False,
        label=_("നിലവിലുള്ള ചിത്രം നീക്കം ചെയ്യുക"),
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )

    def clean_avatar_upload(self):
        f = self.cleaned_data.get('avatar_upload')
        # ImageField has already verified the file decodes as an image (Pillow).
        if f and f.size > 5 * 1024 * 1024:
            raise forms.ValidationError(_("ചിത്രത്തിന്റെ വലുപ്പം 5MB-യിൽ കുറവായിരിക്കണം."))
        return f

    class Meta:
        model = ReaderUser
        fields = [
            'name', 'email', 'phone_number', 'gender', 'gender_other', 'birth_year',  # was 'dob' (renamed field); gender_other added
            'bio',  # profile_image removed from Meta — handled via avatar_upload in save()
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
            'delivery_notes': forms.Textarea(attrs={'rows': 2, 'placeholder': _('ഉദാ: സെക്യൂരിറ്റി ഡെസ്കിൽ ഏൽപ്പിക്കുക'), 'class': 'form-input'}),
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
                phone_str = str(decrypted_phone).strip()
                try:
                    # 1. Parse to a PhoneNumber object with 'IN' as the default region.
                    #    This handles both E.164 ("+919876543210") and national-format
                    #    ("9876543210") numbers stored before E.164 normalisation was
                    #    enforced, making pre-population robust to the storage format.
                    # 2. Pass the *object* (not the string) to decompress().
                    #    SplitPhoneNumberWidget.decompress() checks:
                    #      isinstance(value, PhoneNumber) → fast path, no re-parse
                    #      else → PhoneNumber.from_string(value) — may fail on national fmt
                    #    Passing the object guarantees the fast path in all lib versions.
                    # 3. Store the resulting list in self.initial so MultiWidget.render()
                    #    receives a list and skips its own internal decompress() call.
                    phone_obj = PhoneNumberObj.from_string(phone_str, region='IN')
                    if phone_obj.is_valid():
                        decomposed = self.fields['phone_number'].widget.decompress(phone_obj)
                        self.initial['phone_number'] = decomposed
                    else:
                        # Parsed but not a valid number — surface the raw string so the
                        # user can see and correct it rather than getting a blank field.
                        self.initial['phone_number'] = phone_str
                except Exception:
                    # Last resort: pass the raw string; render-time decompress will try.
                    self.initial['phone_number'] = phone_str

            # Consent checkboxes: same reasoning — put them in self.initial so the
            # bool value is guaranteed to reach CheckboxInput.get_context() correctly.
            # A non-null timestamp means consent is currently active → box ticked.
            self.initial['read_history_consent'] = bool(self.instance.read_history_consent_at)
            self.initial['gender_consent'] = bool(self.instance.gender_consent_at)
            self.initial['birth_year_consent'] = bool(self.instance.birth_year_consent_at)


    def validate_unique(self):
        """
        AbstractUser.clean() calls normalize_email() which maps None → ''.
        That happens inside _post_clean() BEFORE validate_unique() runs, so by the
        time uniqueness is checked, instance.email is already '' even if the field
        was left blank.  A UNIQUE constraint on '' fires if any other row has ''.

        Re-force '' → None here (mirrors CustomWagtailUserEditForm.validate_unique())
        so the DB sees NULL, which SQL UNIQUE allows to appear multiple times.
        """
        if self.instance.email == "":
            self.instance.email = None
        super().validate_unique()

    def save(self, commit=True):
        """
        Custom save with three responsibilities the default ModelForm.save() can't handle:

        1.  Phone number: phone_number is a declared non-model field — Django's
            construct_instance() skips it entirely, so the change would be silently
            discarded.  We call set_phone() here instead to keep phone_number_hash
            (blind index) and phone_number_encrypted (ciphertext) atomically in sync.

        2.  Consent grant / revoke: translates the boolean checkboxes into *_at
            timestamp columns on the model instance:
              checked   (True)  → *_at = timezone.now()  (newly granted)
              unchecked (False) → *_at = None             (revoked)
            No-op when the state has not changed since the last save.

        3.  Read-history purge: when read_history_consent is revoked the
            read_articles M2M is cleared AFTER the row is committed so there is
            never a window where consent_at is already null but history rows still
            exist for record_read_article() to race against.
        """
        # Apply Meta.fields model values (name, email, gender, etc.) to the instance.
        # phone_number (non-model) is skipped by construct_instance(); so are the
        # three consent fields — both are handled manually below.
        instance = super().save(commit=False)
        now = timezone.now()

        # ── 1. Phone ────────────────────────────────────────────────────────────
        phone_val = self.cleaned_data.get('phone_number')
        if phone_val:
            # set_phone() updates both phone_number_hash and phone_number_encrypted
            # atomically; never assign to those fields individually.
            instance.set_phone(str(phone_val))

        # ── 1a-bis. Avatar ───────────────────────────────────────────────────────
        # Wrap a validated upload in a Wagtail Image so renditions keep working,
        # or clear the FK on removal. The previous Image row (file + renditions)
        # is deleted after commit — avatars use related_name='+' and are created
        # one-per-reader here, so they are never shared with page content.
        avatar_file = self.cleaned_data.get('avatar_upload')
        remove_avatar = self.cleaned_data.get('remove_avatar')
        old_avatar = instance.profile_image if (avatar_file or remove_avatar) else None

        if remove_avatar and not avatar_file:
            instance.profile_image = None
        elif avatar_file:
            from wagtail.images import get_image_model
            WagtailImage = get_image_model()
            # forms.ImageField stores the verified Pillow image on the file object.
            pil = getattr(avatar_file, 'image', None)
            width, height = (pil.size if pil is not None else (0, 0))
            new_image = WagtailImage(
                title=f"avatar-{instance.pk or 'new'}-{avatar_file.name}"[:255],
                file=avatar_file,
                width=width,
                height=height,
                uploaded_by_user=instance if instance.pk else None,
            )
            new_image.save()
            instance.profile_image = new_image

        # ── 1b. Email null safety ────────────────────────────────────────────────
        # validate_unique() already fixed this once, but AbstractUser.clean() can
        # re-run between there and here (e.g., when super().save() triggers it again).
        # Force '' → None a final time so the ORM writes NULL, never empty string.
        if not instance.email:
            instance.email = None

        # ── 2. Consent timestamps ────────────────────────────────────────────────
        # `had_*` reads the DB value from BEFORE this form submission.
        # The *_at fields are NOT in Meta.fields so super().save(commit=False)
        # leaves them untouched on the instance — their value here is the original
        # DB value, which is exactly what we need for the before/after comparison.

        # ── Read History ──
        had_read_history = bool(instance.read_history_consent_at)
        wants_read_history = self.cleaned_data.get('read_history_consent', False)

        if wants_read_history and not had_read_history:
            # Newly granted: stamp the timestamp.
            instance.read_history_consent_at = now
        elif not wants_read_history and had_read_history:
            # Revoked: clear the timestamp.
            # The M2M purge happens after commit below to avoid a partial-save race.
            instance.read_history_consent_at = None

        # ── Gender ──
        had_gender = bool(instance.gender_consent_at)
        wants_gender = self.cleaned_data.get('gender_consent', False)

        if wants_gender and not had_gender:
            instance.gender_consent_at = now
        elif not wants_gender and had_gender:
            instance.gender_consent_at = None

        # ── Birth Year ──
        had_birth_year = bool(instance.birth_year_consent_at)
        wants_birth_year = self.cleaned_data.get('birth_year_consent', False)

        if wants_birth_year and not had_birth_year:
            instance.birth_year_consent_at = now
        elif not wants_birth_year and had_birth_year:
            instance.birth_year_consent_at = None

        if commit:
            instance.save()
            # Save any M2M fields declared in Meta.fields (none currently, but
            # future-safe and required to satisfy super().save(commit=False)'s contract).
            self._save_m2m()
            # ── 3. Read-history purge ────────────────────────────────────────────
            # Must run AFTER instance.save() so the consent_at column is already
            # null in the DB before we remove the history rows.
            if not wants_read_history and had_read_history:
                instance.read_articles.clear()
            # ── 4. Old-avatar cleanup ────────────────────────────────────────────
            # Runs after commit so the row never briefly points at a deleted Image.
            # Image.delete() also removes the file and all renditions from storage.
            if old_avatar and old_avatar != instance.profile_image:
                old_avatar.delete()

        return instance


class UpdateInterestsForm(forms.ModelForm):
    """
    Form handling the customization of reader topics and interest classifications.
    """
    # Field name must match the model's M2M field name exactly so ModelForm maps it correctly.
    interested_topics = forms.ModelMultipleChoiceField(  # was ModelMultipleChoiceForm (typo) + wrong name 'interests'
        queryset=Topic.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label=_("നിങ്ങൾക്ക് താൽപ്പര്യമുള്ള വിഷയങ്ങൾ തിരഞ്ഞെടുക്കുക")
    )

    class Meta:
        model = ReaderUser
        fields = ['interested_topics']  # was ['interests'] — FieldError at form init


class CustomWagtailUserCreationForm(UserCreationForm):
    """
    Custom implementation of Wagtail's User Creation form matching structural custom user needs.
    """
    name = forms.CharField(max_length=255, required=True, label=_("പൂർണ്ണ നാമം"))

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
    name = forms.CharField(max_length=255, required=True, label=_("പൂർണ്ണ നാമം"))
    
    grant_access_until = forms.DateField(
        required=False,
        label=_("ഈ തീയതി വരെ താൽക്കാലിക പ്രവേശനം നൽകുക"),
        help_text=_(
            "തിരഞ്ഞെടുത്ത തീയതി വരെ ഈ വായനക്കാരന് സൗജന്യ പ്രവേശനം (പേയ്‌മെന്റ് ഇല്ലാതെ) നൽകുക. "
            "മുകളിലെ പ്ലാനിനെ മറികടക്കും. മാറ്റം വേണ്ടെങ്കിൽ ശൂന്യമായി വിടുക."
        ),
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    class Meta:
        model = User
        fields = {
            'phone_number_hash', 
            'name', 'email', 'is_active', 'gender', 'birth_year',
            'subscription_plan',
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

        # ── Temporary-access validation ──
        until = cleaned_data.get('grant_access_until')
        if until and until < timezone.localdate():
            self.add_error('grant_access_until',
                           _("പ്രവേശന അവസാന തീയതി ഭാവിയിലായിരിക്കണം."))

        # If the plan resolves to complimentary but nothing backs it with a future
        # end, require a date rather than letting the ledger write fail at the DB.
        if cleaned_data.get('subscription_plan') == 'complimentary' and not until:
            existing_end = getattr(self.instance, 'subscription_end', None)
            if not existing_end or existing_end <= timezone.now():
                self.add_error('grant_access_until',
                               _("സൗജന്യ പ്രവേശനം നൽകാൻ ഒരു അവസാന തീയതി നിശ്ചയിക്കുക."))

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

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Mirror validate_unique(): a blank email must persist as NULL, never ''.
        if not instance.email:
            instance.email = None

        until = self.cleaned_data.get('grant_access_until')
        until_dt = None
        if until:
            # End of the chosen day, so access lasts through that whole date.
            until_dt = timezone.make_aware(
                datetime.combine(until, time.max),
                timezone.get_current_timezone(),
            )

        if commit:
            instance.save()
            self._save_m2m()
            if until_dt:
                # Canonical, ledger-aware grant. Runs after the base save so the
                # profile edits land first; this writes the 'complimentary' record.
                instance.grant_temporary_access(until=until_dt)

        return instance