"""View mixins for page chrome and role-based access."""

from django.contrib import messages
from django.contrib.auth.mixins import AccessMixin, LoginRequiredMixin
from django.shortcuts import redirect

# Shared with core.permissions so the web and API agree on who gets in.
ADMIN_ROLES = ("admin",)
MANAGER_ROLES = ("admin", "manager")
INVENTORY_EDITOR_ROLES = ("admin", "manager", "inventory")
# Everyone who works leads, chats and bookings — i.e. not the inventory role.
SALES_ROLES = ("admin", "manager", "employee")


class PageMixin(LoginRequiredMixin):
    """Supplies the heading/subtitle every dashboard template renders."""

    page_title = "Page"
    page_subtitle = ""
    active_nav = ""

    def get_page_title(self):
        return self.page_title

    def get_page_subtitle(self):
        return self.page_subtitle

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("page_title", self.get_page_title())
        ctx.setdefault("page_subtitle", self.get_page_subtitle())
        ctx.setdefault("active_nav", self.active_nav)
        return ctx


class RoleRequiredMixin(AccessMixin):
    """Restricts a view to a set of roles. Superusers always pass."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not self.has_role(request.user):
            messages.error(request, "You do not have permission to open that page.")
            return redirect("dashboard:overview")
        return super().dispatch(request, *args, **kwargs)

    def has_role(self, user):
        if user.is_superuser or not self.allowed_roles:
            return True
        return user.role in self.allowed_roles


class ManagerRequiredMixin(PageMixin, RoleRequiredMixin):
    allowed_roles = MANAGER_ROLES


class AdminRequiredMixin(PageMixin, RoleRequiredMixin):
    allowed_roles = ADMIN_ROLES


class InventoryEditorMixin(PageMixin, RoleRequiredMixin):
    allowed_roles = INVENTORY_EDITOR_ROLES


class SalesRequiredMixin(PageMixin, RoleRequiredMixin):
    """CRM, conversations and bookings: everyone except the inventory role."""

    allowed_roles = SALES_ROLES
