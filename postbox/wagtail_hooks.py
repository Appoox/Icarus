from django.urls import path
from wagtail import hooks
from wagtail.admin.menu import MenuItem


@hooks.register('register_admin_urls')
def register_postbox_admin_urls():
    from postbox import admin_views
    return [
        path('postbox/',          admin_views.postbox_index,  name='icarus_postbox_index'),
        path('postbox/<int:pk>/', admin_views.postbox_detail, name='icarus_postbox_detail'),
    ]


@hooks.register('register_admin_menu_item')
def register_postbox_menu_item():
    from postbox.models import Postbox
    from django.urls import reverse
    count = Postbox.objects.filter(is_reviewed=False).count()
    return MenuItem(
        f'Postbox ({count})' if count else 'Postbox',
        reverse('icarus_postbox_index'),
        icon_name='postbox',
        order=500,
    )