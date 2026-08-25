from django.db import models

from core.models import SoftDeleteModel, TimeStampedModel


class Website(TimeStampedModel, SoftDeleteModel):
    """Registered customer-facing website / brand."""

    name = models.CharField(max_length=150)
    brand_name = models.CharField(max_length=150, blank=True)
    domain = models.CharField(max_length=255, unique=True)
    source_identifier = models.SlugField(max_length=80, unique=True)
    logo = models.ImageField(upload_to="websites/logos/", blank=True, null=True)
    primary_color = models.CharField(max_length=20, blank=True, default="#0F766E")
    widget_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    settings = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.source_identifier})"


class WebsiteAPIKey(TimeStampedModel):
    """API / embed keys for chat widget on a website."""

    website = models.ForeignKey(Website, on_delete=models.CASCADE, related_name="api_keys")
    key_name = models.CharField(max_length=100)
    public_key = models.CharField(max_length=64, unique=True)
    secret_key = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Website API Key"

    def __str__(self):
        return f"{self.website.source_identifier} · {self.key_name}"
