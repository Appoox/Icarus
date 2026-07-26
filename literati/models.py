from django.db import models
from django import forms
from django.core.exceptions import ValidationError
from modelcluster.fields import ParentalKey, ParentalManyToManyField
from modelcluster.contrib.taggit import ClusterTaggableManager
from taggit.models import TaggedItemBase
from wagtail.models import Page, Orderable
from wagtail.fields import RichTextField, StreamField
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, InlinePanel
from phonenumber_field.modelfields import PhoneNumberField
from hitcount.models import HitCountMixin, HitCount
from django.contrib.contenttypes.fields import GenericRelation
from hitcount.views import HitCountMixin as HitCountViewMixin
from wagtail.snippets.models import register_snippet
from reader.models import ReaderUser
from wagtail.search import index
from modelcluster.models import ClusterableModel
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger


class SocialMediaBlock(blocks.StructBlock):
    platform = blocks.ChoiceBlock(choices=[
        ('facebook', 'Facebook'),
        ('twitter', 'Twitter'),
        ('linkedin', 'LinkedIn'),
        ('instagram', 'Instagram'),
        ('github', 'GitHub'),
        ('website', 'Website'),
    ])
    url = blocks.URLBlock()

    class Meta:
        icon = 'link'
        label = 'Social Media Link'

class LiteratiTag(TaggedItemBase):
    content_object = ParentalKey(
        'Literati',
        related_name='tagged_items',
        on_delete=models.CASCADE
    )

class AuthorIndexPageTag(TaggedItemBase):
    content_object = ParentalKey(
        'AuthorIndexPage',
        related_name='tagged_items',
        on_delete=models.CASCADE
    )

class Literati(Page, HitCountMixin):

    parent_page_types = ['AuthorIndexPage']

    role = models.CharField("Title / Role of the person", blank=True)
    bio = RichTextField("Bio", blank=True)
    profile_image = models.ForeignKey(
        'wagtailimages.Image',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    
    email = models.EmailField("Email", blank=True)
    # Controls whether the email address is shown on the public author profile page.
    # Defaults to False (private) — authors must explicitly opt in to exposure.
    show_email = models.BooleanField(
        "Show email publicly",
        default=False,
        help_text="Tick to display this email address on the public author profile page.",
    )

    phone_number = PhoneNumberField("Phone Number", blank=False)
    # Controls whether the phone number is shown on the public author profile page.
    # Defaults to False (private) — phone numbers are sensitive; opt-in only.
    show_phone_number = models.BooleanField(
        "Show phone number publicly",
        default=False,
        help_text="Tick to display this phone number on the public author profile page.",
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
        validators=[ReaderUser.pincode_validator],
        help_text='6-digit Indian pincode.',
    )
    district = models.CharField(max_length=100, blank=True)
    state = models.CharField(
        max_length=50, blank=True,
        choices=ReaderUser.INDIAN_STATES,
    )
    reader_user = models.OneToOneField(
        'reader.ReaderUser',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='literati',
        verbose_name="Associated Reader User"
    )
    areas_of_interest = ParentalManyToManyField('issue.Topic', blank=True)
    social_media_links = StreamField([
        ('social_link', SocialMediaBlock()),
    ], blank=True, use_json_field=True)

    tags = ClusterTaggableManager(through=LiteratiTag, blank=True)

    hit_count_generic = GenericRelation(
        HitCount, object_id_field='object_pk',
        related_query_name='hit_count_generic_relation'
    )
    read_fully_count = models.PositiveIntegerField(default=0, editable=False)

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)

        # Gate contact details behind the author's own visibility preference.
        # Templates use {{ public_phone }} / {{ public_email }} directly —
        # they never need to branch on show_* themselves.
        context['public_phone'] = (
            self.phone_number if self.show_phone_number else None
        )
        context['public_email'] = (
            self.email if self.show_email else None
        )

        # Hit count — skip staff, bot user-agents, and datacenter IPs.
        from home.hit_filters import is_countable_human
        if is_countable_human(request):
            hit_count = HitCount.objects.get_for_object(self)
            HitCountViewMixin().hit_count(request, hit_count)

        return context

    def get_articles(self):
        return [rel.page for rel in self.literati_articles.select_related('page').filter(page__live=True)]

    def clean(self):
        super().clean()

        if not self.phone_number:
            return

        from reader.models import ReaderUser

        # ── ReaderUser phone ─────────────────────────────────────────────────
        # We intentionally do NOT raise a ValidationError when a ReaderUser with
        # the same phone exists. The common real-world case is a reader who later
        # becomes an author — the after_create_page hook auto-links them.
        # For existing pages, the admin can use the "Link / Merge Reader" action.
        # (Phone lookup now uses HMAC hash because the field is encrypted.)

        # ── ReaderUser email — still a hard block ────────────────────────────
        # Unlike phone, we have no auto-merge path for email conflicts.
        if self.email:
            email_qs = ReaderUser.objects.filter(email=self.email)
            if self.reader_user:
                email_qs = email_qs.exclude(pk=self.reader_user.pk)
            if email_qs.exists():
                raise ValidationError({
                    'email': (
                        'A reader account with this email already exists. '
                        'Use "Link / Merge Reader" on the author page to link them.'
                    )
                })

        # ── Literati-to-Literati uniqueness ──────────────────────────────────
        if Literati.objects.filter(phone_number=self.phone_number).exclude(pk=self.pk).exists():
            raise ValidationError({
                'phone_number': 'Another author page is already registered with this phone number.'
            })

        if self.email:
            if Literati.objects.filter(email=self.email).exclude(pk=self.pk).exists():
                raise ValidationError({
                    'email': 'Another author page is already registered with this email.'
                })

    def save(self, *args, **kwargs):
        # ── Invariant: every Literati is backed by a ReaderUser ──────────
        # (The reverse is not true — most readers never become authors.)
        # Enforced at model level so it holds for admin saves, hooks, and
        # scripts alike. Idempotent: looks up by phone hash before creating,
        # so a reader who later becomes an author is linked, not duplicated.
        newly_linked = False
        if not self.reader_user and self.phone_number:
            from reader.models import ReaderUser as _RU
            phone = str(self.phone_number)
            existing = _RU.objects.filter(
                phone_number_hash=_RU.hash_phone(phone)
            ).first()
            if existing:
                self.reader_user = existing
            else:
                # No password argument → unusable password; they log in
                # through the normal phone flow like any other reader.
                self.reader_user = _RU.objects.create_user(
                    phone_number=phone,
                    name=self.title,
                    email=self.email or None,
                )
            newly_linked = True

        if self.reader_user:
            user = self.reader_user
            updated_fields = []

            # Phone: compare via HMAC hash — Fernet ciphertexts are non-deterministic
            # so comparing them directly would always look like a change.
            from reader.models import ReaderUser as _RU
            new_hash = _RU.hash_phone(str(self.phone_number))
            if user.phone_number_hash != new_hash:
                # set_phone() updates both the hash and the ciphertext atomically
                user.set_phone(str(self.phone_number))
                updated_fields.extend(['phone_number_hash', 'phone_number_encrypted'])

            # All plain-text fields — one loop instead of repeated if-blocks
            for literati_attr, user_attr in [
                ('email',          'email'),
                ('title',          'name'),
                ('address_line_1', 'address_line_1'),
                ('address_line_2', 'address_line_2'),
                ('city',           'city'),
                ('post_office',    'post_office'),
                ('pincode',        'pincode'),
                ('district',       'district'),
                ('state',          'state'),
            ]:
                if getattr(user, user_attr, '') != getattr(self, literati_attr, ''):
                    setattr(user, user_attr, getattr(self, literati_attr, ''))
                    updated_fields.append(user_attr)

            if updated_fields:
                user.save(update_fields=updated_fields)

        super().save(*args, **kwargs)

        # A newly linked user may already sit on the current board (page
        # created after the board, or a reader merged into an author page)
        # — reconcile so they receive staff/groups immediately.
        if newly_linked:
            sync_editorial_staff()

    content_panels = [
        FieldPanel('title', heading="Name", help_text="Enter the full name of the person"),
        FieldPanel('slug', help_text="The slug is a URL-friendly name for this page. It is used as the end portion of the page's web address (URL). It should only contain lowercase letters, numbers, and hyphens. Changing the slug will change the URL of the page and may break existing links."),
        FieldPanel('tags'),
        FieldPanel('role'),
        FieldPanel('profile_image'),
        FieldPanel('bio'),
        MultiFieldPanel([
            # Each field is followed immediately by its visibility toggle so the
            # relationship is obvious to editors without reading help text.
            FieldPanel('email'),
            FieldPanel('show_email'),
            FieldPanel('phone_number'),
            FieldPanel('show_phone_number'),
            FieldPanel('reader_user', read_only=True),
        ], heading="Contact Information"),
        MultiFieldPanel([
            FieldPanel('address_line_1'),
            FieldPanel('address_line_2'),
            FieldPanel('city'),
            FieldPanel('post_office'),
            FieldPanel('district'),
            FieldPanel('state'),
            FieldPanel('pincode'),
        ], heading="Mailing Address"),
        FieldPanel('areas_of_interest', widget=forms.CheckboxSelectMultiple),
        FieldPanel('social_media_links'),
    ]

    promote_panels = [
        MultiFieldPanel([
            FieldPanel('seo_title'),
            FieldPanel('search_description'),
        ], heading="For Search Engines"),
        MultiFieldPanel([
            FieldPanel('show_in_menus'),
        ], heading="Settings"),
    ]


class AuthorIndexPage(Page):
    intro = RichTextField(blank=True)
    tags = ClusterTaggableManager(through=AuthorIndexPageTag, blank=True)
    max_count = 1
    subpage_types = ['Literati']

    def get_context(self, request):
        context = super().get_context(request)
        # Use Literati directly — AuthorIndexPage owns Literati children
        authors = Literati.objects.live().order_by('title')

        # 12 per page fills a 4-col or 3-col grid neatly
        paginator = Paginator(authors, 12)
        page_number = request.GET.get('page')
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        context['authors']   = page_obj
        context['page_obj']  = page_obj
        context['paginator'] = paginator
        return context

    content_panels = Page.content_panels + [
        FieldPanel('intro'),
        FieldPanel('tags'),
    ]

class ArticleAuthorRelationship(Orderable):
    # Note: Using string reference to 'articles.Article' to avoid circular imports
    page = ParentalKey('articles.Article', related_name='article_authors', on_delete=models.CASCADE)
    author = models.ForeignKey('Literati', related_name='literati_articles', on_delete=models.CASCADE)
    
    role = models.CharField(
        max_length=100, 
        default="Author", 
        help_text="e.g. Author, Photographer, Illustrator"
    )

    panels = [
        FieldPanel('author'),
        FieldPanel('role'),
    ]

def sync_editorial_staff():
    """
    Reconcile ReaderUser.is_staff and role-Group membership with the
    current editorial board. Idempotent full reconciliation — safe to
    call after any board or membership change, in any order.

    Rules:
      • Active members of the *current* board whose Literati page is
        linked to a ReaderUser get is_staff=True. The grant is recorded
        in ReaderUser.staff_granted_by_board so revocation never touches
        manually-appointed staff or superusers.
      • If a Django auth Group exists whose name exactly matches a role's
        display name ('Editor', 'Associate Editor', 'Managing Editor',
        'Board Member'), this function owns its membership: entitled
        users are added, everyone else is removed. Create the group and
        attach permissions to give that role powers; no group of that
        name → the role carries no permissions. Groups are never
        auto-created. To grant editor-like permissions to a non-board
        user, use a differently named group.
    """
    from django.contrib.auth.models import Group

    role_display = dict(EditorialBoardMember.ROLE_CHOICES)   # value → display
    role_groups = {
        g.name: g for g in Group.objects.filter(name__in=role_display.values())
    }

    # user pk → set of role display names held on the current board
    entitled = {}
    users_by_pk = {}
    memberships = (
        EditorialBoardMember.objects
        .filter(board__is_current=True, is_active=True,
                editor__reader_user__isnull=False)
        .select_related('editor__reader_user')
    )
    for m in memberships:
        user = m.editor.reader_user
        users_by_pk[user.pk] = user
        entitled.setdefault(user.pk, set()).add(role_display[m.role])

    # ── Grant staff to entitled users ────────────────────────────────────
    for user in users_by_pk.values():
        if not user.is_staff:
            user.is_staff = True
            user.staff_granted_by_board = True
            user.save(update_fields=['is_staff', 'staff_granted_by_board'])
        # Already staff for another reason (flag False): leave provenance
        # alone — they keep staff independently of the board.

    # ── Revoke staff we granted from users no longer entitled ───────────
    stale = ReaderUser.objects.filter(
        staff_granted_by_board=True
    ).exclude(pk__in=entitled.keys())
    for user in stale:
        if user.is_superuser:
            # Never demote a superuser; just drop the stale provenance flag.
            user.staff_granted_by_board = False
            user.save(update_fields=['staff_granted_by_board'])
        else:
            user.is_staff = False
            user.staff_granted_by_board = False
            user.save(update_fields=['is_staff', 'staff_granted_by_board'])

    # ── Role-group reconciliation (only for groups the admin created) ───
    for display_name, group in role_groups.items():
        should_have = {pk for pk, roles in entitled.items() if display_name in roles}
        current = set(group.user_set.values_list('pk', flat=True))
        to_add = should_have - current
        to_remove = current - should_have
        if to_add:
            group.user_set.add(*to_add)
        if to_remove:
            group.user_set.remove(*to_remove)


@register_snippet
class EditorialBoard(index.Indexed, ClusterableModel):
    name = models.CharField(max_length=255)
    is_current = models.BooleanField(
        default=False,
        help_text=(
            "Marks this as the sitting editorial board. Saving a board with "
            "this ticked automatically unticks every other board — exactly "
            "one board is current at a time. Old boards are never deleted; "
            "they remain attached to their issues for history."
        ),
    )
    
    search_fields = [
        index.SearchField('name'),
    ]

    panels = [
        FieldPanel('name'),
        FieldPanel('is_current'),
        InlinePanel('members', label="Board Members"),
    ]

    def save(self, *args, **kwargs):
        from django.db import transaction
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_current:
                # Demote every other board — "new board replaces old".
                # .update() bypasses save() so this cannot recurse.
                EditorialBoard.objects.exclude(pk=self.pk).filter(
                    is_current=True
                ).update(is_current=False)
        sync_editorial_staff()

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        # If the deleted board was current, its members lose board-granted
        # staff; reconciliation picks that up.
        sync_editorial_staff()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Editorial Boards"


class EditorialBoardMember(Orderable):
    board = ParentalKey(EditorialBoard, related_name='members', on_delete=models.CASCADE)
    editor = models.ForeignKey('Literati', related_name='board_memberships', on_delete=models.CASCADE)
    
    ROLE_CHOICES = [
        ('editor', 'Editor'),
        ('associate', 'Associate Editor'),
        ('managing', 'Managing Editor'),
        ('board', 'Board Member'),
    ]
    role = models.CharField(
        max_length=50, 
        choices=ROLE_CHOICES, 
        default='editor',
    )

    # ── Membership Tracking ──
    joined_at = models.DateField(
        null=True, blank=True,
        help_text="Date when this member joined the editorial board."
    )
    left_at = models.DateField(
        null=True, blank=True,
        help_text="Date when this member left the editorial board."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this member is currently active on the board."
    )

    panels = [
        FieldPanel('editor'),
        FieldPanel('role'),
        FieldPanel('joined_at'),
        FieldPanel('left_at'),
        FieldPanel('is_active'),
    ]

    def save(self, *args, **kwargs):
        from datetime import date

        is_new = not self.pk
        create_history = False

        # Auto-set joined_at on first creation if not explicitly provided
        if is_new:
            if not self.joined_at:
                self.joined_at = date.today()
            create_history = True
        else:
            try:
                old = EditorialBoardMember.objects.get(pk=self.pk)

                # Deactivation: is_active True → False
                if old.is_active and not self.is_active:
                    if not self.left_at:
                        self.left_at = date.today()
                    # Close the open history entry for this member
                    EditorialBoardMembershipHistory.objects.filter(
                        board=self.board,
                        editor=self.editor,
                        left_at__isnull=True,
                    ).update(left_at=self.left_at)

                # Re-activation: is_active False → True
                elif not old.is_active and self.is_active:
                    self.joined_at = date.today()
                    self.left_at = None
                    create_history = True

            except EditorialBoardMember.DoesNotExist:
                pass

        super().save(*args, **kwargs)

        # Create a new history entry after save so all FKs are populated
        if create_history and self.board_id:
            EditorialBoardMembershipHistory.objects.create(
                board_id=self.board_id,
                editor=self.editor,
                role=self.role,
                joined_at=self.joined_at,
            )

        # Membership changed → reconcile staff flags and role groups.
        sync_editorial_staff()

    def delete(self, *args, **kwargs):
        from datetime import date

        # Removing the row from the board panel counts as leaving: close
        # any open history stint so the record shows when they were removed.
        EditorialBoardMembershipHistory.objects.filter(
            board_id=self.board_id,
            editor_id=self.editor_id,
            left_at__isnull=True,
        ).update(left_at=date.today())

        super().delete(*args, **kwargs)
        sync_editorial_staff()


@register_snippet
class EditorialBoardMembershipHistory(models.Model):
    """
    Immutable audit log of editorial board membership periods.
    A new row is created each time a member joins (or re-joins) a board,
    and the row is closed (left_at stamped) when they leave.
    """
    board = models.ForeignKey(
        EditorialBoard,
        on_delete=models.CASCADE,
        related_name='membership_history',
    )
    editor = models.ForeignKey(
        'Literati',
        on_delete=models.CASCADE,
        related_name='board_membership_history',
    )
    role = models.CharField(
        max_length=50,
        choices=EditorialBoardMember.ROLE_CHOICES,
    )
    joined_at = models.DateField(
        help_text="Date when this membership period began."
    )
    left_at = models.DateField(
        null=True, blank=True,
        help_text="Date when this membership period ended. Null if still active."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    panels = [
        FieldPanel('board'),
        FieldPanel('editor'),
        FieldPanel('role'),
        FieldPanel('joined_at'),
        FieldPanel('left_at'),
    ]

    class Meta:
        ordering = ['-joined_at']
        verbose_name = "Board Membership History"
        verbose_name_plural = "Board Membership History"

    def __str__(self):
        status = f"left {self.left_at}" if self.left_at else "active"
        return f"{self.editor.title} — {self.get_role_display()} ({self.joined_at} – {status})"