import hmac
import hashlib

from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.signals import user_logged_in
from django.conf import settings
from django.dispatch import receiver
from django.core.validators import RegexValidator
from django.utils import timezone
from datetime import timedelta

from wagtail.admin.panels import FieldPanel, MultiFieldPanel, FieldRowPanel
from wagtail.snippets.models import register_snippet
from wagtail.search import index
from phonenumber_field.modelfields import PhoneNumberField
from encrypted_fields.fields import EncryptedCharField
from kalapila.models import Comment

from django.contrib.auth.signals import user_login_failed, user_logged_in
from django.dispatch import receiver
from django.core.cache import cache
from django.db.models import Q

# ── Single source of truth for plan configuration ───────────────────────
PLANS = {
    '1_month':  {'name': '1 Month',   'price': 299,  'has_print': False, 'duration_days': 30},
    '3_months': {'name': '3 Months',  'price': 799,  'has_print': False, 'duration_days': 90},
    '6_months': {'name': '6 Months',  'price': 1499, 'has_print': True,  'duration_days': 180},
    '1_year':   {'name': '1 Year',    'price': 2499, 'has_print': True,  'duration_days': 365},
    '3_years':   {'name': '3 Years',   'price': 5999, 'has_print': True,  'duration_days': 1095},
    '5_years':   {'name': '5 Years',   'price': 7999, 'has_print': True,  'duration_days': 1825},
}

class ReaderUserManager(BaseUserManager):
    def create_user(self, phone_number, name, password=None, **extra_fields):
        if not phone_number:
            raise ValueError('The Phone Number must be set')
        if not name:
            raise ValueError('The Name must be set')

        user = self.model(name=name, **extra_fields)
        # Use set_phone() so the hash and encryption are both populated atomically.
        user.set_phone(str(phone_number))
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(phone_number, name, password, **extra_fields)

class ReaderUser(AbstractUser, index.Indexed):
    """
    Custom User model for Icarus. Consolidates Django User and Reader profile.
    Tracks subscription status, payment details, reading history, and topic interests.
    """
    username = None
    name = models.CharField(max_length=255)

    # ── Phone number stored as HMAC hash (for lookups) + Fernet encryption (for display) ──
    # The raw E.164 string never touches the DB in plaintext, so a stolen pg_dump
    # leaks nothing usable. The hash is stable (same input → same output) so exact-match
    # lookups still work for login and uniqueness checks.
    phone_number_hash = models.CharField(
        "Phone Number (Hash)",
        max_length=64,
        unique=True,
        help_text="HMAC-SHA256 of the phone number. Used for fast, secure exact-match lookups.",
    )
    phone_number_encrypted = EncryptedCharField(
        "Phone Number (Encrypted)",
        max_length=255,
        blank=True,
        help_text="Fernet-encrypted phone number. Unreadable in raw DB dumps.",
    )

    email = models.EmailField(unique=True, blank=True, null=True)

    objects = ReaderUserManager()
    profile_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text='Visible next to your comments.'
    )
    bio = models.TextField(
        max_length=500, 
        blank=True, 
        help_text='A short description about yourself.'
    )
    can_comment = models.BooleanField(
        default=True,
        help_text='Uncheck this to restrict this user from posting comments.'
    )

    # def own_comments(self):
    #     return Comment.objects.filter(user=self)
    
    # ── Compliance & Legal (DPDP & GDPR) ──
    # Single source of truth for current document versions
    CURRENT_TERMS_VERSION = "1.0.0"
    CURRENT_PRIVACY_VERSION = "1.0.0"

    accepted_terms_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of agreement to Terms of Service for legal enforceability."
    )
    terms_version = models.CharField(
        max_length=20, blank=True,
        help_text="Specific version of Terms of Service agreed to by the user."
    )
    accepted_privacy_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of explicit consent to the Privacy Policy."
    )
    privacy_version = models.CharField(
        max_length=20, blank=True,
        help_text="Specific version of Privacy Policy consented to (Required for Consent Versioning)."
    )
    # ── Age Declaration (Signup Consent) ──
    is_above_18 = models.BooleanField(
        default=False,
        help_text="Self-declaration that the user is 18 years or older, captured at signup for DPDP compliance."
    )
    age_declaration_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of the 18+ age self-declaration at signup."
    )

    # ── Explicit Consent for Optional Profile Fields ──
    birth_year_consent_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of explicit consent to share birth year for demographic analytics."
    )
    gender_consent_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of explicit consent to share gender information."
    )
    read_history_consent_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of explicit consent to track reading history. Cleared on revocation."
    )

    newsletter_opt_in = models.BooleanField(
        default=False,
        help_text="User's choice to receive non-essential marketing communications."
    )
    newsletter_opt_in_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp of when the newsletter consent status last changed."
    )
    
    # ── Security & Audit ──
    registration_ip = models.GenericIPAddressField(
        null=True, blank=True,
        help_text="Captured at signup to detect and prevent fraudulent bot registrations."
    )
    last_login_ip = models.GenericIPAddressField(
        null=True, blank=True,
        help_text="Monitored for security auditing and to detect unauthorized account access."
    )
    
    # ── Account Lifecycle ──
    deactivated_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Marked when a user requests deletion; triggers the statutory data retention clock."
    )
    is_verified = models.BooleanField(
        default=False,
        help_text="Account status based on successful verification of primary contact method."
    )

    USERNAME_FIELD = 'phone_number_hash'
    REQUIRED_FIELDS = ['name', 'email']

    # ── Phone utility methods ───────────────────────────────────────────
    @staticmethod
    def hash_phone(raw_number: str) -> str:
        """
        Produce a stable HMAC-SHA256 of the phone number.
        Uses PHONE_HASH_KEY (separate from SECRET_KEY) so the two can be
        rotated independently. Always call this instead of constructing the
        HMAC inline.
        """
        return hmac.new(
            settings.PHONE_HASH_KEY.encode(),
            raw_number.encode(),
            hashlib.sha256,
        ).hexdigest()

    def set_phone(self, raw_number: str) -> None:
        """
        Hash and encrypt the phone number atomically.
        Always call this instead of assigning to the fields directly —
        it guarantees hash and ciphertext are never out of sync.
        """
        normalized = str(raw_number).strip()
        self.phone_number_hash = self.hash_phone(normalized)
        self.phone_number_encrypted = normalized

    @property
    def username(self):
        """
        Wagtail and some internal Django apps expect a string 'username'.
        Return the human-readable encrypted value, not the internal HMAC hash.
        """
        return self.phone_number_encrypted or ''

    def get_username(self):
        """
        Override Django's default which would return the USERNAME_FIELD value
        (the HMAC hash). Templates, the Wagtail toolbar, and log output all
        call this — they should see the real phone number, not a 64-char hex string.
        """
        return self.phone_number_encrypted or ''

    def natural_key(self):
        """Required for some serialization flows. Uses the hash (the DB key)."""
        return (self.phone_number_hash,)

    def get_full_name(self):
        """Used by Wagtail and Django admin to display the user's name."""
        return self.name or self.phone_number_encrypted or ''

    def get_short_name(self):
        return self.name or self.phone_number_encrypted or ''

    GENDER_CHOICES = [
        ('പുരുഷന്‍', 'Male'),
        ('സ്ത്രീ', 'Female'),
        ('നോണ്‍ ബൈനറി / ക്വീര്‍', 'Non-binary / Queer'),
        ('പറയാന്‍ താല്‍പര്യമില്ല', 'Prefer not to say'),
        ('മറ്റൊന്ന്', 'Other')
    ]

    gender = models.CharField(max_length=100, choices=GENDER_CHOICES, blank=True)
    gender_other = models.CharField("Other Gender", max_length=100, blank=True)

    # Birth year only — the full date (day + month) is not needed for any use case
    # here and is a meaningfully higher-risk identifier when combined with name.
    # A plain integer is safe, indexable, and directly usable for GROUP BY age-bracket queries.
    birth_year = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Year of birth only. Sufficient for demographic analytics; safer than full DOB.",
    )

    # ── Address ──────────────────────────────────────────────────────
    INDIAN_STATES = [
        ('', '— Select State —'),
        ('Andaman and Nicobar Islands', 'Andaman and Nicobar Islands'),
        ('Andhra Pradesh', 'Andhra Pradesh'),
        ('Arunachal Pradesh', 'Arunachal Pradesh'),
        ('Assam', 'Assam'),
        ('Bihar', 'Bihar'),
        ('Chandigarh', 'Chandigarh'),
        ('Chhattisgarh', 'Chhattisgarh'),
        ('Dadra and Nagar Haveli and Daman and Diu', 'Dadra and Nagar Haveli and Daman and Diu'),
        ('Delhi', 'Delhi'),
        ('Goa', 'Goa'),
        ('Gujarat', 'Gujarat'),
        ('Haryana', 'Haryana'),
        ('Himachal Pradesh', 'Himachal Pradesh'),
        ('Jammu and Kashmir', 'Jammu and Kashmir'),
        ('Jharkhand', 'Jharkhand'),
        ('Karnataka', 'Karnataka'),
        ('Kerala', 'Kerala'),
        ('Ladakh', 'Ladakh'),
        ('Lakshadweep', 'Lakshadweep'),
        ('Madhya Pradesh', 'Madhya Pradesh'),
        ('Maharashtra', 'Maharashtra'),
        ('Manipur', 'Manipur'),
        ('Meghalaya', 'Meghalaya'),
        ('Mizoram', 'Mizoram'),
        ('Nagaland', 'Nagaland'),
        ('Odisha', 'Odisha'),
        ('Puducherry', 'Puducherry'),
        ('Punjab', 'Punjab'),
        ('Rajasthan', 'Rajasthan'),
        ('Sikkim', 'Sikkim'),
        ('Tamil Nadu', 'Tamil Nadu'),
        ('Telangana', 'Telangana'),
        ('Tripura', 'Tripura'),
        ('Uttar Pradesh', 'Uttar Pradesh'),
        ('Uttarakhand', 'Uttarakhand'),
        ('West Bengal', 'West Bengal'),
    ]

    pincode_validator = RegexValidator(
        regex=r'^[1-9][0-9]{5}$',
        message='Enter a valid 6-digit Indian pincode.',
    )

    address_line_1 = models.CharField(
        max_length=255, blank=True,
        help_text='House / flat number, street name.',
    )
    address_line_2 = models.CharField(
        max_length=255, blank=True,
        help_text='Landmark, area, locality.',
    )
    city = models.CharField(max_length=100, blank=True)

    post_office = models.CharField(max_length=100, blank=True)
    
    pincode = models.CharField(
        max_length=6, blank=True,
        validators=[pincode_validator],
        help_text='6-digit Indian pincode.',
    )

    district = models.CharField(max_length=100, blank=True)

    state = models.CharField(
        max_length=50, blank=True,
        choices=INDIAN_STATES,
    )

    # ചേര്‍ക്കുന്ന ആളുടെ വിവരങ്ങൾ
    care_of_name = models.CharField(max_length=255, blank=True)
    care_of_number = PhoneNumberField("C/O Phone Number", blank=True)
    care_of_district = models.CharField(max_length=100, blank=True)
    care_of_meghala = models.CharField(max_length=100, blank=True)
    care_of_unit = models.CharField(max_length=100, blank=True)

    search_fields = [
        index.SearchField('name'),
        index.SearchField('email'),
        # phone_number_hash is intentionally excluded — the hash is not human-readable,
        # and phone_number_encrypted cannot be searched at the DB level.
        # Admins can look up readers by name or email instead.
        index.SearchField('care_of_name'),
        index.SearchField('care_of_district'),
        index.SearchField('care_of_meghala'),
        index.SearchField('care_of_unit'),
    ]

    # ── Subscription ──────────────────────────────────────────────────
    SUBSCRIPTION_PLANS = [
        ('none', 'No Subscription'),
        ('complimentary', 'Complimentary (Temporary Access)'),
        ('1_month', '1 Month'),
        ('3_months', '3 Months'),
        ('6_months', '6 Months'),
        ('1_year', '1 Year'),
        ('3_years', '3 Years'),
        ('5_years', '5 Years'),
    ]

    PLAN_DURATIONS = {
        'none':     timedelta(days=0),
        '1_month':  timedelta(days=30),
        '3_months': timedelta(days=90),
        '6_months': timedelta(days=180),
        '1_year':   timedelta(days=365),
        '3_years':   timedelta(days=1095),
        '5_years':   timedelta(days=1825),
    }

    subscription_plan = models.CharField(
        max_length=20,
        choices=SUBSCRIPTION_PLANS,
        default='none',
        help_text='Active subscription plan.',
    )
    subscription_start = models.DateTimeField(
        null=True, blank=True,
        help_text='When the current subscription period began.',
    )
    subscription_end = models.DateTimeField(
        null=True, blank=True,
        help_text='When the current subscription period expires.',
    )
    is_cancelled = models.BooleanField(
        default=False,
        help_text="Flag indicating if the active subscription has been cancelled but remains in grace period."
    )
    is_anonymized = models.BooleanField(
        default=False,
        help_text="Flag indicating if this account has been anonymized under DPDPA/GDPR rules."
    )

    # ✅ NEW: Grace period for failed/lapsed renewals (e.g. 3 days leeway)
    GRACE_PERIOD = timedelta(days=3)

    @property
    def is_profile_complete(self):
        """
        Returns True if the essential profile fields are filled.
        Used to prompt users to complete their profile after signup.
        """
        # Essential fields for a "complete" profile
        essential_fields = [
            self.gender,
            self.birth_year,      # was date_of_birth — year-only is sufficient
            self.address_line_1,
            self.city,
            self.state,
            self.pincode
        ]
        return all(field for field in essential_fields)

    @property
    def is_subscribed(self):
        """True if the reader has an active, non-expired subscription."""
        if self.subscription_plan == 'none':
            return False
        if self.subscription_end is None:
            return False
        return timezone.now() < self.subscription_end

    @property
    def is_in_grace_period(self):
        """
        True if the subscription has just expired but is within the grace window.
        Useful to show a softer 'renew now' prompt instead of hard-locking content.
        """
        if self.subscription_plan == 'none' or self.subscription_end is None:
            return False
        now = timezone.now()
        return self.subscription_end <= now < (self.subscription_end + self.GRACE_PERIOD)

    @property
    def days_until_expiry(self):
        """Returns the number of days left in the subscription, or None."""
        if not self.is_subscribed:
            return None
        delta = self.subscription_end - timezone.now()
        return max(delta.days, 0)

    def status_display(self):
        if self.is_subscribed:
            return "Active"
        if self.is_in_grace_period:
            return "Grace Period"
        if self.subscription_plan != 'none':
            return "Expired"
        return "No Subscription"
    status_display.short_description = "Status"

    def activate_subscription(self, plan_type):
        """
        Activates or extends a reader's subscription based on the plan type.
        Now also handles automated print delivery activation.
        """
        plan = PLANS.get(plan_type)
        if not plan:
            return

        duration = timedelta(days=plan['duration_days'])
        now = timezone.now()

        if self.is_subscribed and self.subscription_end:
            new_start = self.subscription_end
        else:
            new_start = now

        self.subscription_plan = plan_type
        self.subscription_start = new_start
        self.subscription_end = new_start + duration
        self.is_cancelled = False
        self.save(update_fields=[
            'subscription_plan', 'subscription_start', 'subscription_end', 'is_cancelled',
        ])

    def grant_temporary_access(self, until=None, days=None):
        """
        Grant complimentary (unpaid) access — an editor comp or free trial.
        Records a distinct 'complimentary' entry in the subscription ledger.

        Provide either `until` (an aware datetime) or `days` (int). Access starts
        now. Payment details are left untouched — a comp grant attaches no payment.
        """
        now = timezone.now()
        if until is None:
            if not days:
                return
            until = now + timedelta(days=days)

        self.subscription_plan = 'complimentary'
        self.subscription_start = now
        self.subscription_end = until
        self.is_cancelled = False
        self.save(update_fields=[
            'subscription_plan', 'subscription_start', 'subscription_end', 'is_cancelled',
        ])

    # ── Payment ───────────────────────────────────────────────────────
    payment_details = models.OneToOneField(
        'PaymentDetails',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reader_user',
    )

    # ── Reading History & Interests ───────────────────────────────────
    read_articles = models.ManyToManyField(
        'articles.Article',
        blank=True,
        related_name='readers',
        help_text='Articles this reader has accessed.',
    )
    interested_topics = models.ManyToManyField(
        'issue.Topic',
        blank=True,
        related_name='interested_readers',
        help_text="Topics this reader is interested in.",
    )

    # ── Favourites ────────────────────────────────────────────────────
    favorite_articles = models.ManyToManyField(
        'articles.Article',
        blank=True,
        related_name='favorited_by',
        help_text='Articles this reader has bookmarked.',
    )
    favorite_issues = models.ManyToManyField(
        'issue.Issue',
        blank=True,
        related_name='favorited_by',
        help_text='Issues this reader has bookmarked.',
    )

    # ── Reading History Consent Gate ──────────────────────────────────

    @property
    def tracking_consent(self):
        """
        Returns True if the user has consented to reading history tracking.

        The source of truth is read_history_consent_at: a non-null timestamp
        means opted in; null (cleared on revocation/deactivation) means opted out.
        This is a read-only derived property — never assign to it directly.
        To grant consent: set read_history_consent_at = timezone.now() and save.
        To revoke consent: set read_history_consent_at = None and save
        (deactivate() already does this on account closure).
        """
        return self.read_history_consent_at is not None

    def record_read_article(self, article):
        """
        Single enforcement point for reading history consent.

        Records an article as read ONLY if the user has explicitly opted in via
        the signup form's read_history_consent checkbox.
        Always call this instead of user.read_articles.add(article) directly
        so the consent gate can never be accidentally bypassed.
        """
        if self.tracking_consent:
            self.read_articles.add(article)

    def phone_display(self):
        """
        Returns the decrypted phone number for Wagtail admin list_display.
        Wagtail calls this as a callable, so it must be a method, not a property.
        """
        return self.phone_number_encrypted or '—'
    phone_display.short_description = "Phone"

    panels = [
        MultiFieldPanel([
            FieldPanel('name'),
            FieldPanel('email'),
            # phone_number_encrypted is read-only here; editing goes through the
            # snippet edit form which calls set_phone() to keep hash in sync.
            FieldPanel('phone_number_encrypted', read_only=True),
            FieldPanel('gender'),
            FieldPanel('gender_other'),
            FieldPanel('birth_year'),   # was date_of_birth
        ], heading="Personal Information"),
        MultiFieldPanel([
            FieldPanel('profile_image'),
            FieldPanel('bio'),
            FieldPanel('can_comment'),
        ], heading="Public Profile & Moderation"),
        MultiFieldPanel([
            FieldPanel('care_of_name'),
            FieldPanel('care_of_number'),
            FieldPanel('care_of_district'),
            FieldPanel('care_of_meghala'),
            FieldPanel('care_of_unit'),
        ], heading="Added By"),
        MultiFieldPanel([
            FieldPanel('address_line_1'),
            FieldPanel('address_line_2'),
            FieldRowPanel([
                FieldPanel('city'),
                FieldPanel('post_office'),
            ]),
            FieldRowPanel([
                FieldPanel('district'),
                FieldPanel('state'),
            ]),
            FieldPanel('pincode'),
        ], heading="Mailing Address"),
        MultiFieldPanel([
            FieldPanel('is_print_subscriber'),
            FieldPanel('print_delivery_status'),
            FieldPanel('print_expiry_date'),
            FieldPanel('delivery_notes'),
        ], heading="Print Circulation Management"),
        MultiFieldPanel([
            FieldPanel('subscription_plan'),
            FieldPanel('is_cancelled'),
            FieldRowPanel([
                FieldPanel('subscription_start'),
                FieldPanel('subscription_end'),
            ]),
        ], heading="Subscription Status"),
        FieldPanel('payment_details'),
        MultiFieldPanel([
            FieldPanel('read_articles'),
            FieldPanel('interested_topics'),
        ], heading="Activity & Interests"),
        MultiFieldPanel([
            FieldPanel('favorite_articles'),
            FieldPanel('favorite_issues'),
        ], heading="Favourites"),
        MultiFieldPanel([
            FieldPanel('accepted_terms_at', read_only=True),
            FieldPanel('terms_version', read_only=True),
            FieldPanel('accepted_privacy_at', read_only=True),
            FieldPanel('privacy_version', read_only=True),
            FieldPanel('is_above_18', read_only=True),
            FieldPanel('age_declaration_at', read_only=True),
            FieldPanel('birth_year_consent_at', read_only=True),     # was dob_consent_at
            FieldPanel('gender_consent_at', read_only=True),
            FieldPanel('read_history_consent_at', read_only=True),   # new
            FieldPanel('newsletter_opt_in'),
            FieldPanel('registration_ip', read_only=True),
            FieldPanel('last_login_ip', read_only=True),
            FieldPanel('deactivated_at', read_only=True),
            FieldPanel('is_verified'),
            FieldPanel('is_anonymized'),
        ], heading="Legal, Security & Lifecycle"),
    ]

    def deactivate(self):
        """
        Executes a soft-deactivation. Revokes access immediately and starts
        the statutory retention clock for data purging. Revoking read_history_consent_at
        prevents any further tracking from the moment of deactivation.
        """
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.newsletter_opt_in = False
        self.newsletter_opt_in_at = timezone.now()
        # Revoke read history tracking consent on account closure.
        # Actual history M2M rows are cleared in anonymize_account() per the retention schedule.
        self.read_history_consent_at = None
        self.save(update_fields=[
            'is_active', 'deactivated_at',
            'newsletter_opt_in', 'newsletter_opt_in_at',
            'read_history_consent_at',
        ])

    def anonymize_account(self):
        """
        Complies with DPDPA 'Right to Erasure' and GDPR 'Right to be Forgotten'.
        Deletes credentials, profile data, and tracking logs, but preserves the financial
        ledger and subscription history in an anonymized state.
        """
        self.set_unusable_password()
        self.name = "Archived User"
        self.email = f"archived_user_{self.id}@icarus.internal"
        # Replace both phone fields with non-reversible dummy values.
        dummy_phone = f"+999{self.id:011d}"
        self.set_phone(dummy_phone)

        self.profile_image = None
        self.bio = ""
        self.gender = ""
        self.gender_other = ""
        self.birth_year = None      # was date_of_birth

        # Clear all consent timestamps
        self.birth_year_consent_at = None   # was dob_consent_at
        self.gender_consent_at = None
        self.age_declaration_at = None
        self.read_history_consent_at = None

        # Clear addresses
        self.address_line_1 = ""
        self.address_line_2 = ""
        self.city = ""
        self.post_office = ""
        self.pincode = ""
        self.district = ""
        self.state = ""

        # Clear Care Of fields
        self.care_of_name = ""
        self.care_of_number = ""
        self.care_of_district = ""
        self.care_of_meghala = ""
        self.care_of_unit = ""

        # Clear activity, favorites, and read history
        if hasattr(self, 'read_articles') and self.read_articles:
            self.read_articles.clear()
        if hasattr(self, 'interested_topics') and self.interested_topics:
            self.interested_topics.clear()
        if hasattr(self, 'favorite_articles') and self.favorite_articles:
            self.favorite_articles.clear()
        if hasattr(self, 'favorite_issues') and self.favorite_issues:
            self.favorite_issues.clear()

        self.newsletter_opt_in = False
        self.registration_ip = None
        self.last_login_ip = None

        self.is_active = False
        self.is_anonymized = True
        self.save()

        # Delete audit logs / tracking logs associated with this user
        from auditlog.models import LogEntry
        LogEntry.objects.filter(actor_id=self.id).delete()

        from django.contrib.contenttypes.models import ContentType
        user_content_type = ContentType.objects.get_for_model(self.__class__)
        LogEntry.objects.filter(content_type=user_content_type, object_id=self.id).delete()

    # ── Print Subscription Management ──
    is_print_subscriber = models.BooleanField(
        default=False,
        help_text="Designates whether this user receives the physical print magazine."
    )
    print_delivery_status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active Delivery'),
            ('paused', 'Paused'),
            ('returned', 'Address Issue / Returned'),
            ('inactive', 'No Print Copy'),
        ],
        default='inactive',
        help_text="Current logistical status of the print delivery."
    )
    print_expiry_date = models.DateField(
        null=True, blank=True,
        help_text="When the print subscription ends (may differ from digital expiry)."
    )
    delivery_notes = models.TextField(
        blank=True,
        help_text="Special instructions for the courier (e.g., 'Leave at gate')."
    )

    def save(self, *args, **kwargs):
        """
        Override save to handle audit logging for consent changes
        and manage subscription history tracking.
        """
        # Ensure that if a user is deactivated, we don't accidentally re-activate them 
        # unless specifically intended via the admin.
        if self.deactivated_at and not self.is_active:
             self.newsletter_opt_in = False

        old_plan = 'none'
        old_start = None
        old_end = None
        old_payment = None
        old_cancelled = False

        if self.pk:
            # Check if opt-in status has changed to update the audit timestamp
            try:
                old_instance = ReaderUser.objects.get(pk=self.pk)
                if old_instance.newsletter_opt_in != self.newsletter_opt_in:
                    self.newsletter_opt_in_at = timezone.now()
                
                # Capture old subscription details for history tracking and audit trail synchronization.
                old_plan = old_instance.subscription_plan
                old_start = old_instance.subscription_start
                old_end = old_instance.subscription_end
                old_payment = old_instance.payment_details
                old_cancelled = old_instance.is_cancelled
            except ReaderUser.DoesNotExist:
                pass
        elif self.newsletter_opt_in:
            # New user who opted in immediately
            self.newsletter_opt_in_at = timezone.now()
            

        if self.subscription_plan != 'none':
            if self.subscription_start is None:
                self.subscription_start = timezone.now()
            if self.subscription_end is None:
                plan = PLANS.get(self.subscription_plan)
                if plan:
                    self.subscription_end = self.subscription_start + timedelta(
                        days=plan['duration_days']
                    )
                    

        super().save(*args, **kwargs)

        # ── Subscription History Tracking ──
        # Check if subscription info changed
        subscription_changed = (
            old_plan != self.subscription_plan or
            old_start != self.subscription_start or
            old_end != self.subscription_end or
            old_payment != self.payment_details or
            old_cancelled != self.is_cancelled
        )

        if subscription_changed or (old_plan == 'none' and self.subscription_plan != 'none'):
            if self.subscription_plan == 'none':
                # Subscription cancelled or cleared.
                # Deactivate all active history records for this user
                active_histories = SubscriptionHistory.objects.filter(reader=self, is_active=True)
                for history in active_histories:
                    # Mark as inactive and cancelled in the database log.
                    # Audit trail for tax compliance and user support.
                    # Preserving these logs ensures tax authorities can verify historic transactions,
                    # and customer support can trace billing issues.
                    history.is_active = False
                    if old_plan != 'none':
                        history.is_cancelled = True
                        if self.subscription_end:
                            history.subscription_end = self.subscription_end
                    history.save()
            else:
                # Subscription activated, renewed, or updated.
                # Find if there is an active history record with the same plan to update.
                active_history = SubscriptionHistory.objects.filter(
                    reader=self, is_active=True, subscription_plan=self.subscription_plan
                ).first()

                if active_history:
                    # Update fields on the existing active history record.
                    # Audit trail for tax compliance and user support.
                    # This ensures modifications to dates or payments on the active plan are captured.
                    active_history.subscription_start = self.subscription_start
                    active_history.subscription_end = self.subscription_end
                    active_history.payment_details = self.payment_details
                    active_history.is_cancelled = self.is_cancelled
                    active_history.save()
                else:
                    # Deactivate all other active history records first.
                    SubscriptionHistory.objects.filter(reader=self, is_active=True).update(is_active=False)

                    # Create a new active history record.
                    # Audit trail for tax compliance and user support.
                    # When a subscriber activates a new plan type or starts a new cycle, we write a
                    # new log entry to preserve the billing record and subscription dates.
                    SubscriptionHistory.objects.create(
                        reader=self,
                        subscription_plan=self.subscription_plan,
                        subscription_start=self.subscription_start,
                        subscription_end=self.subscription_end,
                        payment_details=self.payment_details,
                        is_active=True,
                        is_cancelled=self.is_cancelled
                    )

    class Meta:
        verbose_name = 'Reader User'
        verbose_name_plural = 'Reader Users'

    def __str__(self):
        # Fall back gracefully if the encrypted field hasn't been populated yet.
        phone_display = self.phone_number_encrypted or f"user #{self.pk}"
        return f'{self.name or phone_display} ({self.email or phone_display})'


class SubscriptionHistory(models.Model):
    """
    Tracks the historical records of reader subscriptions.
    Acts as a secure ledger of all user subscription intervals.
    """
    reader = models.ForeignKey(
        'ReaderUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='subscription_history_records',
        help_text='The reader associated with this subscription record.'
    )
    subscription_plan = models.CharField(
        max_length=20,
        choices=ReaderUser.SUBSCRIPTION_PLANS,
        help_text='Subscription plan type.'
    )
    subscription_start = models.DateTimeField(
        help_text='When this subscription period began.'
    )
    subscription_end = models.DateTimeField(
        help_text='When this subscription period expires/expired.'
    )
    payment_details = models.ForeignKey(
        'PaymentDetails',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='subscription_history_records',
        help_text='Payment transaction details associated with this subscription.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    # DB Flags
    is_active = models.BooleanField(
        default=True,
        help_text="Flag indicating if this is currently the active subscription record."
    )
    is_cancelled = models.BooleanField(
        default=False,
        help_text="Flag indicating if this subscription was cancelled prematurely."
    )

    panels = [
        FieldPanel('reader', read_only=True),
        FieldPanel('subscription_plan', read_only=True),
        FieldRowPanel([
            FieldPanel('subscription_start', read_only=True),
            FieldPanel('subscription_end', read_only=True),
        ]),
        FieldPanel('payment_details', read_only=True),
        FieldRowPanel([
            FieldPanel('is_active', read_only=True),
            FieldPanel('is_cancelled', read_only=True),
        ]),
    ]

    class Meta:
        verbose_name = 'Subscription History'
        verbose_name_plural = 'Subscription Histories'
        ordering = ['-subscription_start']

    def __str__(self):
        return f"{self.reader} - {self.get_subscription_plan_display()} ({self.subscription_start.date()} to {self.subscription_end.date()})"

class PaymentDetails(models.Model):
    """
    Structured payment information, designed for future integration
    with gateways like Cashfree or BillDesk.
    """

    PAYMENT_METHODS = [
        ('card', 'Credit / Debit Card'),
        ('upi', 'UPI'),
        ('netbanking', 'Net Banking'),
        ('wallet', 'Wallet'),
        ('other', 'Other'),
    ]

    PAYMENT_STATUSES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    gateway_name = models.CharField(max_length=50, blank=True)
    gateway_transaction_id = models.CharField(max_length=255, blank=True)
    gateway_order_id = models.CharField(max_length=255, blank=True)
    idempotency_key = models.CharField(
        max_length=255, 
        unique=True, 
        null=True, blank=True,
        help_text="Unique key to prevent duplicate payment processing."
    )

    payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHODS, default='card',
    )
    payment_method_other = models.CharField("Other Payment Method", max_length=100, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(
        max_length=20, choices=PAYMENT_STATUSES, default='pending',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    panels = [
        MultiFieldPanel([
            FieldPanel('gateway_name'),
            FieldPanel('gateway_transaction_id'),
            FieldPanel('gateway_order_id'),
        ], heading="Gateway Reference"),
        MultiFieldPanel([
            FieldPanel('payment_method'),
            FieldPanel('payment_method_other'),
            FieldRowPanel([
                FieldPanel('amount'),
                FieldPanel('currency'),
            ]),
            FieldPanel('status'),
        ], heading="Payment Info"),
        MultiFieldPanel([
            FieldPanel('created_at', read_only=True),
            FieldPanel('updated_at', read_only=True),
        ], heading="Timestamps"),
    ]

    class Meta:
        verbose_name = 'Payment Details'
        verbose_name_plural = 'Payment Details'

    def __str__(self):
        return (
            f'{self.gateway_name or "—"} · {self.get_status_display()} '
            f'· ₹{self.amount}'
        )
@receiver(user_logged_in)
def update_user_login_ip(sender, request, user, **kwargs):
    """
    Tracks the IP address used during login for security and audit purposes.
    Ensures we only track for ReaderUser to avoid crashing on staff login.
    """
    if not isinstance(user, ReaderUser):
        return

    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    
    user.last_login_ip = ip
    user.save(update_fields=['last_login_ip'])


# ── Native Auditlog Tracking Registration ─────────────────────────────
from auditlog.registry import auditlog

if not auditlog.contains(ReaderUser):
    auditlog.register(ReaderUser, m2m_fields={'groups'})

# ── Tracking Failed Login Attempts ─────────────────────────────────────
@receiver(user_login_failed)
def track_failed_login(sender, credentials, request, **kwargs):
    """
    Tracks consecutive failed login attempts in the cache framework.
    Inspects all credential values (excluding passwords) to find the correct ReaderUser.
    """
    # 1. Output a debug line to your terminal console to see exactly what Allauth sends
    print(f"\n[DEBUG] Failed login attempt detected. Credentials sent: {credentials}\n")

    user = None
    
    # 2. Iterate through all submitted credential values to find a matching phone/email
    for key, value in credentials.items():
        if key in ('password', 'password1', 'password2') or not value:
            continue
            
        value_str = str(value).strip()
        
        # Hash the submitted value so we can look it up against the blind index.
        # The endswith partial match is intentionally removed — it is impossible
        # to suffix-search an HMAC hash, and the phone_number column no longer exists.
        hashed = ReaderUser.hash_phone(value_str)
        user = ReaderUser.objects.filter(
            Q(phone_number_hash=hashed) |
            Q(email__iexact=value_str)
        ).first()
        
        if user:
            break
    
    if not user:
        print("[DEBUG] Could not match any credential value to a ReaderUser in the database.")
        return

    # 3. Save the incremented failed attempts count to cache
    cache_key = f"consecutive_failed_logins_{user.pk}"
    attempts = cache.get(cache_key, 0) + 1
    cache.set(cache_key, attempts, timeout=86400)  # Reset after 24 hours
    
    print(f"[DEBUG] Logged failed attempt #{attempts} for user ID {user.pk} ({user.name})")


@receiver(user_logged_in)
def reset_failed_login_attempts(sender, request, user, **kwargs):
    """
    Clears the consecutive failed attempts count when the user successfully signs in.
    """
    if isinstance(user, ReaderUser):
        cache_key = f"consecutive_failed_logins_{user.pk}"
        cache.delete(cache_key)
        print(f"[DEBUG] Successful login: Cleared failed attempts counter for user ID {user.pk}")