from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base with created/updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Abstract soft-delete support."""

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class AttributionFields(models.Model):
    """Where a visitor came from: UTM tags, referring page and landing page.

    Captured on the Conversation (widget) or Lead (website form) and copied
    forward to the Lead and Booking, so reports can credit the campaign.
    Values arrive from browsers: see `core.attribution.clean_attribution`.
    """

    utm_source = models.CharField(max_length=100, blank=True)
    utm_medium = models.CharField(max_length=100, blank=True)
    utm_campaign = models.CharField(max_length=150, blank=True)
    utm_term = models.CharField(max_length=150, blank=True)
    utm_content = models.CharField(max_length=150, blank=True)
    referrer = models.CharField(max_length=500, blank=True)
    landing_page = models.CharField(max_length=500, blank=True)

    class Meta:
        abstract = True
