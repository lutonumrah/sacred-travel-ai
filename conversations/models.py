from django.conf import settings
from django.db import models

from core.models import AttributionFields, TimeStampedModel
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


class Conversation(TimeStampedModel, AttributionFields):
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
    # When the chat last moved to WAITING; drives the AI auto-resume timer.
    handoff_requested_at = models.DateTimeField(null=True, blank=True)
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


class AIProvider(models.TextChoices):
    ANTHROPIC = "anthropic", "Anthropic (Claude)"
    GEMINI = "gemini", "Google (Gemini)"


# Offered in the settings dropdown. Any other ID can still be typed in by hand.
CLAUDE_MODELS = [
    ("claude-opus-5-5", "Claude Opus 5.5 — recommended"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5 — faster, half the price"),
    ("claude-haiku-4-5", "Claude Haiku 4.5 — fastest, cheapest"),
    ("claude-fable-5-1", "Claude Fable 5.1 — most capable, most expensive"),
    ("claude-opus-5", "Claude Opus 5"),
]
GEMINI_MODELS = [
    ("gemini-3.8-flash", "Gemini 3.8 Flash — recommended"),
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite — cheapest"),
    ("gemini-3.7-flash", "Gemini 3.7 Flash"),
    ("gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)"),
]
MODELS_BY_PROVIDER = {
    AIProvider.ANTHROPIC: CLAUDE_MODELS,
    AIProvider.GEMINI: GEMINI_MODELS,
}
DEFAULT_MODEL = {
    AIProvider.ANTHROPIC: "claude-opus-5-5",
    AIProvider.GEMINI: "gemini-3.8-flash",
}


class AISettings(TimeStampedModel):
    """Which AI writes the chat replies. A single row, edited by admins.

    Keys saved here take precedence over ANTHROPIC_API_KEY / GEMINI_API_KEY in
    the environment, which remain the fallback when a field is left blank.
    """

    enabled = models.BooleanField(default=True)
    provider = models.CharField(
        max_length=20, choices=AIProvider.choices, default=AIProvider.ANTHROPIC
    )
    model = models.CharField(max_length=100, default=DEFAULT_MODEL[AIProvider.ANTHROPIC])
    anthropic_api_key = models.CharField(max_length=255, blank=True)
    gemini_api_key = models.CharField(max_length=255, blank=True)
    # Auto-resume timers. 0 switches a timer off.
    handoff_wait_minutes = models.PositiveIntegerField(
        default=15,
        help_text="If nobody picks up a handoff within this many minutes, the AI resumes.",
    )
    agent_idle_minutes = models.PositiveIntegerField(
        default=0,
        help_text="If the agent leaves a customer message unanswered this long, the AI resumes.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "AI settings"
        verbose_name_plural = "AI settings"

    def __str__(self):
        return f"AI settings ({self.get_provider_display()} · {self.model})"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """The settings row, unsaved defaults if an admin has never saved one."""
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    def stored_key(self, provider):
        if provider == AIProvider.GEMINI:
            return self.gemini_api_key
        return self.anthropic_api_key


class KnowledgeCategory(models.TextChoices):
    POLICY = "policy", "Policy"
    CANCELLATION = "cancellation", "Cancellation"
    PAYMENT = "payment", "Payment"
    FAQ = "faq", "FAQ"
    TRAVEL_INFO = "travel_info", "Travel information"
    OTHER = "other", "Other"


class KnowledgeArticle(TimeStampedModel):
    """Business facts the chat AI may quote: policies, FAQs, travel information.

    The AI answers policy questions only from these articles (see
    `conversations.ai`), so a missing article means "I don't know", never a guess.
    """

    title = models.CharField(max_length=200)
    category = models.CharField(
        max_length=20, choices=KnowledgeCategory.choices, default=KnowledgeCategory.FAQ
    )
    content = models.TextField()
    keywords = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated words or phrases customers use for this, e.g. "
        "“refund, cancel, money back”. Lets the rule-based replies find it.",
    )
    website = models.ForeignKey(
        Website,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="knowledge_articles",
        help_text="Leave blank to use it on every website.",
    )
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["category", "title"]

    def __str__(self):
        return self.title
