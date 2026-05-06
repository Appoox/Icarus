from allauth.account.forms import LoginForm, SignupForm
from allauth.account.adapter import get_adapter

class CustomLoginForm(LoginForm):
    """
    Replicates the signup page's phone field logic for the login form
    and ensures all fields have consistent styling.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'login' in self.fields:
            self.fields['login'] = get_adapter().phone_form_field(label="Phone Number")
            # Restrict to numeric input
            if hasattr(self.fields['login'].widget, 'widgets'):
                self.fields['login'].widget.widgets[1].attrs.update({
                    'inputmode': 'numeric',
                    'pattern': '[0-9]*',
                    'oninput': "this.value = this.value.replace(/[^0-9]/g, '');"
                })
        
        for field_name, field in self.fields.items():
            if field_name != 'login':
                existing_class = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"{existing_class} form-input".strip()

class CustomSignupForm(SignupForm):
    """
    Ensures all allauth signup fields have consistent styling.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'phone' in self.fields:
            # Restrict to numeric input
            if hasattr(self.fields['phone'].widget, 'widgets'):
                self.fields['phone'].widget.widgets[1].attrs.update({
                    'inputmode': 'numeric',
                    'pattern': '[0-9]*',
                    'oninput': "this.value = this.value.replace(/[^0-9]/g, '');"
                })

        for field_name, field in self.fields.items():
            if field_name != 'phone':
                existing_class = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f"{existing_class} form-input".strip()
