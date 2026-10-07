from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView

from core.mixins import AdminRequiredMixin, PageMixin
from core.selectors import paginate
from core.services import log_audit

from . import selectors, services
from .forms import (
    NotificationPreferencesForm,
    PasswordResetForm,
    SetPasswordForm,
    TeamForm,
    UserCreateForm,
    UserFilterForm,
    UserUpdateForm,
)
from .models import Team, User


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # The seed_demo credentials are a development aid, never a live hint.
        ctx["show_demo_login"] = settings.DEBUG
        return ctx


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("accounts:login")


class PasswordChangeView(PageMixin, auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:password_change_done")
    page_title = "My Account"


    page_subtitle = "Update your password and how you hear about new work."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("prefs_form", NotificationPreferencesForm(instance=self.request.user))
        return ctx


class PasswordChangeDoneView(PageMixin, auth_views.PasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"
    page_title = "Password Changed"
    page_subtitle = "Your new password is active."


class PreferencesUpdateView(LoginRequiredMixin, View):
    """The signed-in user's own email address and notification opt-in."""

    def post(self, request):
        form = NotificationPreferencesForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            log_audit(
                actor=request.user,
                action="user.preferences_update",
                entity=request.user,
                metadata={"email_notifications": request.user.email_notifications},
                request=request,
            )
            messages.success(request, "Your notification settings were saved.")
        else:
            messages.error(request, "Enter a valid email address.")
        return redirect("accounts:password_change")


# --- Forgotten password (no login) ------------------------------------------


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    form_class = PasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = SetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        log_audit(
            actor=None,
            action="user.password_reset",
            entity=form.user,
            metadata={"username": form.user.get_username()},
            request=self.request,
        )
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


class UserListView(AdminRequiredMixin, TemplateView):
    template_name = "accounts/user_list.html"
    page_title = "Users & Roles"
    page_subtitle = "Manage admin, manager, employee and inventory users."
    active_nav = "users"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = UserFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.list_users(
            q=data.get("q", ""),
            role=data.get("role", ""),
            status=data.get("status", ""),
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["total"] = queryset.count()
        return ctx


class UserCreateView(AdminRequiredMixin, CreateView):
    model = User
    form_class = UserCreateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:users")
    page_title = "Add User"
    page_subtitle = "Create a system user and assign their role."
    active_nav = "users"

    def form_valid(self, form):
        response = super().form_valid(form)
        services.record_user_saved(
            user=self.object, actor=self.request.user, request=self.request, created=True
        )
        messages.success(self.request, f"User {self.object.username} created.")
        return response


class UserUpdateView(AdminRequiredMixin, UpdateView):
    model = User
    form_class = UserUpdateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:users")
    page_title = "Edit User"
    page_subtitle = "Update profile details, role and access."
    active_nav = "users"

    def form_valid(self, form):
        response = super().form_valid(form)
        services.record_user_saved(
            user=self.object, actor=self.request.user, request=self.request
        )
        messages.success(self.request, f"User {self.object.username} updated.")
        return response


class TeamListView(AdminRequiredMixin, TemplateView):
    template_name = "accounts/team_list.html"
    page_title = "Teams"
    page_subtitle = "Teams used for lead and conversation assignment."
    active_nav = "users"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["teams"] = selectors.list_teams()
        return ctx


class TeamCreateView(AdminRequiredMixin, CreateView):
    model = Team
    form_class = TeamForm
    template_name = "accounts/team_form.html"
    success_url = reverse_lazy("accounts:teams")
    page_title = "Add Team"
    page_subtitle = "Group users for assignment and reporting."
    active_nav = "users"

    def form_valid(self, form):
        response = super().form_valid(form)
        services.record_team_saved(
            team=self.object, actor=self.request.user, request=self.request, created=True
        )
        messages.success(self.request, "Team created.")
        return response


class TeamUpdateView(AdminRequiredMixin, UpdateView):
    model = Team
    form_class = TeamForm
    template_name = "accounts/team_form.html"
    success_url = reverse_lazy("accounts:teams")
    page_title = "Edit Team"
    page_subtitle = "Update team name, description and members."
    active_nav = "users"

    def form_valid(self, form):
        response = super().form_valid(form)
        services.record_team_saved(
            team=self.object, actor=self.request.user, request=self.request
        )
        messages.success(self.request, "Team updated.")
        return response


class AuditLogView(AdminRequiredMixin, TemplateView):
    template_name = "accounts/audit_log.html"
    page_title = "Audit Log"
    page_subtitle = "Sensitive actions recorded across the system."
    active_nav = "users"

    def get_context_data(self, **kwargs):
        from .models import AuditLog

        ctx = super().get_context_data(**kwargs)
        ctx["page_obj"] = paginate(
            AuditLog.objects.select_related("actor"), self.request.GET.get("page"), 50
        )
        return ctx
