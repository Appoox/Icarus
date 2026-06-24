from wagtail import hooks
from wagtail.admin.viewsets.pages import PageListingViewSet
from articles.wagtail_hooks import HitCountColumn, AnalyticsColumn
from .models import AuthorIndexPage
from wagtail.models import Page 

@hooks.register('construct_page_chooser_queryset')
def order_literati_in_chooser(pages, request):
    """
    Order literati pages newest-first inside the page chooser modal
    (e.g. when selecting an author from an article or any other page reference).
    """
    page_type = request.GET.get('page_type')
    is_target_chooser = False

    if page_type and 'literati' in page_type.lower():
        is_target_chooser = True
    else:
        parent_id = None
        if request.resolver_match and 'parent_page_id' in request.resolver_match.kwargs:
            parent_id = request.resolver_match.kwargs.get('parent_page_id')
        else:
            parent_id = request.GET.get('child_of') or request.GET.get('parent')
        
        if parent_id:
            try:
                parent = Page.objects.get(pk=parent_id)
                if parent.specific_class and issubclass(parent.specific_class, AuthorIndexPage):
                    is_target_chooser = True
                    
            except Page.DoesNotExist:
                pass

    if is_target_chooser:
        pages = pages.order_by('-latest_revision_created_at')
    return pages
    
class LiteratiPageListingViewSet(PageListingViewSet):
    icon = 'user'
    menu_label = 'Authors'
    menu_order = 201
    add_to_admin_menu = True

    @property
    def columns(self):
        return super().columns + [
            HitCountColumn("hit_count", label="Views", sort_key="hit_count_generic__hits"),
            AnalyticsColumn("read_fully_count", "read_fully_count", label="Read Fully", sort_key="read_fully_count")
        ]

@hooks.register('register_admin_viewset')
def register_literati_viewset():
    from .models import Literati
    LiteratiPageListingViewSet.model = Literati
    return LiteratiPageListingViewSet('literati')


import logging

logger = logging.getLogger(__name__)

def send_sms_notification(phone_number, message):
    """
    Helper function to send SMS notification.
    Currently prints to console/logs as no SMS gateway is connected.
    Can be integrated with services like Twilio, Gupshup, etc.
    """
    logger.info(f"Preparing to send SMS to {phone_number}: {message}")
    print(f"\n--- SMS PREPARED TO SEND ---\nTo: {phone_number}\nMessage: {message}\n-----------------------------\n")


@hooks.register('after_create_page')
def after_create_literati(request, page):
    from .models import Literati
    if isinstance(page, Literati):
        from reader.models import ReaderUser
        from django.contrib import messages
        from django.core.mail import send_mail
        from django.conf import settings

        if not page.reader_user:
            # Generate password: pattern username+123
            clean_phone = str(page.phone_number).replace(' ', '').replace('-', '')
            password = f"{clean_phone}123"

            try:
                # Create the ReaderUser
                reader_user = ReaderUser.objects.create_user(
                    phone_number=page.phone_number,
                    name=page.title,
                    email=page.email or None,
                    password=password,
                    address_line_1=page.address_line_1,
                    address_line_2=page.address_line_2,
                    city=page.city,
                    post_office=page.post_office,
                    pincode=page.pincode,
                    district=page.district,
                    state=page.state
                )
                
                # Associate with Literati
                page.reader_user = reader_user
                page.save_revision()
                page.save(update_fields=['reader_user'])
                
                # Prepare and send notifications
                email_sent = False
                if page.email:
                    subject = "Your ശാസ്ത്രഗതി Reader Account Credentials"
                    email_message = f"""Dear {page.title},

An author/literati page has been created for you on ശാസ്ത്രഗതി. A reader account has been automatically created for you.

Here are your account credentials to log in:
Phone Number (Username): {page.phone_number}
Password: {password}

You can log in to your account here: {request.build_absolute_uri('/')}

Best regards,
The ശാസ്ത്രഗതി Team
"""
                    try:
                        send_mail(
                            subject,
                            email_message,
                            settings.DEFAULT_FROM_EMAIL,
                            [page.email],
                            fail_silently=False,
                        )
                        email_sent = True
                    except Exception as email_err:
                        logger.error(f"Failed to send credentials email to {page.email}: {email_err}")

                # SMS Notification (stub)
                sms_message = f"Dear {page.title}, your ശാസ്ത്രഗതി reader account is ready. Username: {page.phone_number}, Password: {password}"
                sms_sent = False
                try:
                    send_sms_notification(page.phone_number, sms_message)
                    sms_sent = True
                except Exception as sms_err:
                    logger.error(f"Failed to send credentials SMS to {page.phone_number}: {sms_err}")

                # Admin feedback message
                msg = f"Reader user auto-created for {page.title} (Phone: {page.phone_number}, Password: {password})."
                if email_sent:
                    msg += " Credentials sent via email."
                elif page.email:
                    msg += " Failed to send credentials email."
                else:
                    msg += " No email address provided."
                
                if sms_sent:
                    msg += " SMS prepared and logged."
                
                messages.success(request, msg)

            except Exception as e:
                logger.error(f"Error auto-creating ReaderUser for Literati {page.title}: {e}")
                messages.error(request, f"Error auto-creating Reader User: {e}")
