from django import forms
from django.conf import settings
from django.contrib.auth import forms as auth_forms
from django.contrib.auth.forms import UserCreationForm
from django.urls import reverse

from core.emails import absolute_url, send_email
from core.forms import StyledFormMixin, StyledModelForm
from core.services import log_audit

from .models import Team, User


class UserCreateForm(StyledFormMixin, UserCreationForm):
    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "phone",
            "role",
            "is_active_employee",
            "email_notifications",
            "avatar",
        )


class UserUpdateForm(StyledModelForm):
    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "role",
            "is_active_employee",
            "is_active",
            "email_notifications",
            "avatar",
        )


class NotificationPreferencesForm(StyledModelForm):
    """What a user may change about their own account besides the password."""

    class Meta:
        model = User
        fields = ("email", "email_notifications")
        help_texts = {"email": "Where password resets and notification emails go."}


class PasswordResetForm(StyledFormMixin, auth_forms.PasswordResetForm):
    """Django's reset form, sent through core.emails with a SITE_URL link.

    The link is built from SITE_URL, never the request's Host header. Unknown
    addresses get the same "check your inbox" page as known ones.
    """

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        user = context["user"]
        path = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": context["uid"], "token": context["token"]},
        )
        sent = send_email(
            to=to_email,
            template="password_reset",
            context={
                "user": user,
                "reset_url": absolute_url(path),
                "expiry_hours": max(settings.PASSWORD_RESET_TIMEOUT // 3600, 1),
            },
        )
        log_audit(
            actor=None,
            action="user.password_reset_email" if sent else "user.password_reset_email_failed",
            entity=user,
            metadata={"username": user.get_username()},
        )


class SetPasswordForm(StyledFormMixin, auth_forms.SetPasswordForm):
    pass


class UserFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    role = forms.ChoiceField(required=False, choices=[])
    status = forms.ChoiceField(
        required=False,
        choices=[("", "Any status"), ("active", "Active"), ("inactive", "Inactive")],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import UserRole

        self.fields["role"].choices = [("", "All roles")] + list(UserRole.choices)


class TeamForm(StyledModelForm):
    class Meta:
        model = Team
        fields = ("name", "description", "members", "is_active")
        widgets = {"members": forms.SelectMultiple(attrs={"size": 8})}
