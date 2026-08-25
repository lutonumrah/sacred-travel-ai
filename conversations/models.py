from django.conf import settings
from django.db import models

from core.models import TimeStampedModel
from crm.models import Customer, Lead
from websites.models import Website


class ConversationStatus(models.TextChoices):
    AI_ACTIVE = "ai_active", "AI Active"
    HUMAN_ACTIVE = "human_active", "Human Active"
    WAITING = "waiting", "Waiting"
    CLOSED = "closed", "Closed"


class MessageSender(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    AI = "ai", "AI"
    AGENT = "agent", "Agent"
    SYSTEM = "system", "System"


class Conversation(TimeStampedModel):
    """AI / human chat session tied to a website and optional lead."""

    website = models.ForeignKey(
        Website,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    session_key = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20,
        choices=ConversationStatus.choices,
        default=ConversationStatus.AI_ACTIVE,
        db_index=True,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_conversations",
    )
    context = models.JSONField(default=dict, blank=True)
    requirements = models.JSONField(default=dict, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at"]

    def __str__(self):
        return f"Conversation {self.session_key} [{self.status}]"


class Message(TimeStampedModel):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender_type = models.CharField(max_length=20, choices=MessageSender.choices)
    sender_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    is_internal = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender_type}: {self.content[:40]}"


class ConversationHandoff(TimeStampedModel):
    """Track AI → human takeover and AI resume events."""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="handoffs",
    )
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversation_handoffs",
    )
    reason = models.CharField(max_length=255, blank=True)
    resumed_ai_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Handoff {self.conversation_id}"


class Recommendation(TimeStampedModel):
    """Inventory item recommended inside a conversation."""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="recommendations",
    )
    inventory_type = models.CharField(max_length=20)
    object_id = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")
    payload = models.JSONField(default=dict, blank=True)
    is_selected = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
