from django.contrib.auth.models import AbstractUser
from django.db import models

from core.models import TimeStampedModel


class UserRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    EMPLOYEE = "employee", "Employee"
    INVENTORY = "inventory", "Inventory User"


class User(AbstractUser):
    """System user with role-based access."""

    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.EMPLOYEE,
    )
    phone = models.CharField(max_length=20, blank=True)
    is_active_employee = models.BooleanField(default=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    # Copy in-app notifications to the user's inbox (core.notifications).
    email_notifications = models.BooleanField(
        default=True,
        help_text="Also email lead, handoff, payment and follow-up notifications.",
    )

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"

    @property
    def is_admin(self):
        return self.role == UserRole.ADMIN or self.is_superuser

    @property
    def is_manager(self):
        return self.role in {UserRole.ADMIN, UserRole.MANAGER} or self.is_superuser


class Team(TimeStampedModel):
    """Optional team grouping for lead/conversation assignment."""

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    members = models.ManyToManyField(User, related_name="teams", blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AuditLog(TimeStampedModel):
    """Lightweight audit trail for sensitive actions."""

    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100, blank=True)
    entity_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} by {self.actor_id}"
