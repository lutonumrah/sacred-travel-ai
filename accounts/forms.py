from django import forms
from django.contrib.auth.forms import UserCreationForm

from core.forms import StyledFormMixin, StyledModelForm

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
            "avatar",
        )


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
