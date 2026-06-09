from wagtail.users.views.users import UserViewSet as WagtailUserViewSet

# Import the full implementations from forms.py.
# The old names (CustomUserCreationForm / CustomUserEditForm) matched stubs in
# wagtail_hooks.py that had no implementation — those stubs have been removed.
from .forms import CustomWagtailUserCreationForm, CustomWagtailUserEditForm


class ReaderUserViewSet(WagtailUserViewSet):
    def get_form_class(self, for_update=False):
        if for_update:
            return CustomWagtailUserEditForm
        return CustomWagtailUserCreationForm