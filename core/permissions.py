"""DRF permission classes mirroring the role mixins in `core.mixins`."""

from rest_framework.permissions import BasePermission

from .mixins import ADMIN_ROLES, INVENTORY_EDITOR_ROLES, MANAGER_ROLES, SALES_ROLES


class HasRole(BasePermission):
    """Authenticated user whose role is in `allowed_roles`. Superusers always pass."""

    allowed_roles = ()
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or not self.allowed_roles:
            return True
        return user.role in self.allowed_roles


class IsAdmin(HasRole):
    allowed_roles = ADMIN_ROLES


class IsManager(HasRole):
    allowed_roles = MANAGER_ROLES


class IsInventoryEditor(HasRole):
    allowed_roles = INVENTORY_EDITOR_ROLES


class IsSalesTeam(HasRole):
    allowed_roles = SALES_ROLES
