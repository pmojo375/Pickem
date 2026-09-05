from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email

from .models import UserProfile

User = get_user_model()


class AccountNameForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name")
        labels = {
            "username": "Username",
            "first_name": "First name",
            "last_name": "Last name",
        }
        widgets = {
            "username": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "Username",
                    "autocomplete": "username",
                }
            ),
            "first_name": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "First name",
                    "autocomplete": "given-name",
                }
            ),
            "last_name": forms.TextInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "Last name",
                    "autocomplete": "family-name",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = False
        self.fields["last_name"].required = False

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        if not username:
            raise forms.ValidationError("Enter a username.")
        taken = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError("That username is already taken.")
        return username


class SecondaryEmailForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("secondary_email",)
        labels = {
            "secondary_email": "Secondary email",
        }
        help_texts = {
            "secondary_email": (
                "Optional. Pick reminders, league invites, and season opt-in emails "
                "are sent to both your primary and secondary addresses."
            ),
        }
        widgets = {
            "secondary_email": forms.EmailInput(
                attrs={
                    "class": "input input-bordered w-full",
                    "placeholder": "you@example.com",
                    "autocomplete": "email",
                }
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["secondary_email"].required = False

    def clean_secondary_email(self):
        raw = (self.cleaned_data.get("secondary_email") or "").strip()
        if not raw:
            return ""
        try:
            validate_email(raw)
        except DjangoValidationError as exc:
            raise forms.ValidationError("Enter a valid email address.") from exc
        email = raw.lower()
        primary = ((self.user.email if self.user else "") or "").strip().lower()
        if primary and email == primary:
            raise forms.ValidationError(
                "Secondary email must be different from your primary email."
            )
        return email


class PersonalInviteSignupForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "Choose a username",
                "autocomplete": "username",
            }
        ),
    )
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "Password",
                "autocomplete": "new-password",
            }
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "Confirm password",
                "autocomplete": "new-password",
            }
        ),
    )

    def __init__(self, *args, invite_email=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invite_email = invite_email

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        if not username:
            raise forms.ValidationError("Enter a username.")
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_password1(self):
        password = self.cleaned_data.get("password1")
        if password:
            validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned


class PersonalInviteSetPasswordForm(forms.Form):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "Password",
                "autocomplete": "new-password",
            }
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(
            attrs={
                "class": "input input-bordered w-full",
                "placeholder": "Confirm password",
                "autocomplete": "new-password",
            }
        ),
    )

    def clean_password1(self):
        password = self.cleaned_data.get("password1")
        if password:
            validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned
