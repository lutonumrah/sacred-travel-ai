from django.conf import settings
from django.db import models

from core.models import SoftDeleteModel, TimeStampedModel
from websites.models import Website


class LeadStatus(models.TextChoices):
    NEW = "new", "New"
    QUALIFIED = "qualified", "Qualified"
    INTERESTED = "interested", "Interested"
    PAYMENT_PENDING = "payment_pending", "Payment Pending"
    CONVERTED = "converted", "Converted"
    LOST = "lost", "Lost"
    FOLLOW_UP = "follow_up", "Follow-up"


class LeadSource(models.TextChoices):
    AI_CHAT = "ai_chat", "AI Chat"
    WEBSITE_FORM = "website_form", "Website Form"
    MANUAL = "manual", "Manual"
    PHONE = "phone", "Phone"
    EMAIL = "email", "Email"
    OTHER = "other", "Other"


class Customer(TimeStampedModel, SoftDeleteModel):
    """Central customer profile."""

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    whatsapp = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default="India")
    preferred_language = models.CharField(max_length=50, blank=True, default="en")
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class Lead(TimeStampedModel, SoftDeleteModel):
    """Travel lead with pipeline status."""

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="leads",
        null=True,
        blank=True,
    )
    website = models.ForeignKey(
        Website,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    title = models.CharField(max_length=200)
    status = models.CharField(
        max_length=30,
        choices=LeadStatus.choices,
        default=LeadStatus.NEW,
        db_index=True,
    )
    source = models.CharField(
        max_length=30,
        choices=LeadSource.choices,
        default=LeadSource.AI_CHAT,
    )
    destination = models.CharField(max_length=150, blank=True)
    travel_start = models.DateField(null=True, blank=True)
    travel_end = models.DateField(null=True, blank=True)
    travelers_count = models.PositiveSmallIntegerField(default=1)
    budget_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preferences = models.JSONField(default=dict, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_leads",
    )
    assigned_team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    score = models.PositiveSmallIntegerField(default=0)
    lost_reason = models.CharField(max_length=255, blank=True)
    converted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} [{self.status}]"


class LeadNote(TimeStampedModel):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_notes",
    )
    body = models.TextField()
    is_internal = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note on {self.lead_id}"


class LeadActivity(TimeStampedModel):
    """Interaction / status history for a lead."""

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_activities",
    )
    activity_type = models.CharField(max_length=80)
    summary = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Lead activities"

    def __str__(self):
        return f"{self.activity_type}: {self.summary}"


class FollowUpTask(TimeStampedModel):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="follow_ups")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="follow_up_tasks",
    )
    title = models.CharField(max_length=200)
    due_at = models.DateTimeField()
    reminder_at = models.DateTimeField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    # Set when the scheduler's reminder fires (crm.services.send_due_reminders),
    # so each task is reminded exactly once.
    reminded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_at"]

    def __str__(self):
        return f"{self.title} ({'done' if self.is_completed else 'open'})"
