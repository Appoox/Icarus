# reader/auth_forms.py

from allauth.account.forms import LoginForm, SignupForm
from allauth.account.adapter import get_adapter

class CustomLoginForm(LoginForm):
    """
    Replicates the signup page's phone field logic for the login form,
    ensures consistent styling, and tracks failed login attempt counters.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 1. Set up custom login (phone) field and apply robust sub-widget styling/validation
        if 'login' in self.fields:
            self.fields['login'] = get_adapter().phone_form_field(label="Phone Number")
            field = self.fields['login']
            
            # Style split widgets if present, otherwise style the single main widget
            if hasattr(field.widget, 'widgets'):
                for widget in field.widget.widgets:
                    existing_class = widget.attrs.get('class', '')
                    widget.attrs['class'] = f"{existing_class} form-input".strip()
                
                # Restrict national number input (index 1) to numeric only
                if len(field.widget.widgets) > 1:
                    field.widget.widgets[1].attrs.update({
                        'inputmode': 'numeric',
                        'pattern': '[0-9]*',
                        'oninput': "this.value = this.value.replace(/[^0-9]/g, '');"
                    })
            else:
                existing_class = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"{existing_class} form-input".strip()
        
        # 2. Apply classes to all other fields
        for field_name, field in self.fields.items():
            if field_name != 'login':
                existing_class = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"{existing_class} form-input".strip()

    @property
    def failed_attempts(self):
        """
        Calculates the failed login attempt count.
        Handles both SplitPhoneNumberField inputs (login_0/login_1) and standard inputs.
        """
        # A. Reconstruct the input username/phone from bound data
        login_val = self.data.get('login')
        if not login_val and self.is_bound:
            # Safely grab the national phone number from the split widget
            login_1 = self.data.get('login_1', '').strip()
            if login_1:
                login_val = login_1

        if not login_val:
            # On a fresh GET request, if django-axes is active, check attempts by client IP
            try:
                from django.conf import settings
                if 'axes' in settings.INSTALLED_APPS:
                    from axes.helpers import get_failures
                    if hasattr(self, 'request') and self.request:
                        return get_failures(self.request)
            except Exception:
                pass
            return 0

        # B. Query the user to find the standardized database identifier
        user = None
        try:
            from django.contrib.auth import get_user_model
            from django.db.models import Q
            User = get_user_model()
            # Match either exact email or phone number ending with the entered national number
            user = User.objects.filter(Q(phone_number__endswith=login_val) | Q(email=login_val)).first()
        except Exception:
            pass

        # C. Query django-axes first (if active)
        try:
            from django.conf import settings
            if 'axes' in settings.INSTALLED_APPS:
                from axes.helpers import get_failures
                if hasattr(self, 'request') and self.request:
                    username = str(user.phone_number) if user else login_val
                    credentials = {'username': username}
                    return get_failures(self.request, credentials)
        except Exception:
            pass

        # D. Fall back to your lightweight cache tracker
        if user:
            try:
                from django.core.cache import cache
                cache_key = f"consecutive_failed_logins_{user.pk}"
                return cache.get(cache_key, 0)
            except Exception:
                pass

        return 0

    @property
    def max_attempts(self):
        """
        Returns the maximum allowed login attempts configured for lock out.
        """
        try:
            from django.conf import settings
            if 'axes' in settings.INSTALLED_APPS:
                from axes.helpers import get_failure_limit
                if hasattr(self, 'request') and self.request:
                    return get_failure_limit(self.request)
        except Exception:
            pass
        return 5  # Fallback limit matching your custom cache signal count

    @property
    def lockout_duration_text(self):
        """
        Returns a human-readable lockout duration.
        """
        try:
            from django.conf import settings
            if 'axes' in settings.INSTALLED_APPS:
                cooloff = getattr(settings, 'AXES_COOLOFF_TIME', None)
                if cooloff:
                    if hasattr(cooloff, 'total_seconds'):
                        minutes = int(cooloff.total_seconds() / 60)
                        if minutes >= 60:
                            hours = minutes // 60
                            return f"{hours} hour" + ("s" if hours > 1 else "")
                        return f"{minutes} minute" + ("s" if minutes > 1 else "")
                    return f"{cooloff} hour(s)"
        except Exception:
            pass
        return "24 hours"  # Fallback duration matching your custom cache signal period

class CustomSignupForm(SignupForm):
    """
    Ensures all allauth signup fields have consistent styling
    and restricts phone number input to numeric only.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'phone' in self.fields:
            field = self.fields['phone']
            if hasattr(field.widget, 'widgets'):
                for widget in field.widget.widgets:
                    existing_class = widget.attrs.get('class', '')
                    widget.attrs['class'] = f"{existing_class} form-input".strip()
                # Restrict national number input (index 1) to numeric only
                if len(field.widget.widgets) > 1:
                    field.widget.widgets[1].attrs.update({
                        'inputmode': 'numeric',
                        'pattern': '[0-9]*',
                        'oninput': "this.value = this.value.replace(/[^0-9]/g, '');"
                    })
            else:
                existing_class = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"{existing_class} form-input".strip()

        for field_name, field in self.fields.items():
            if field_name != 'phone':
                existing_class = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"{existing_class} form-input".strip()