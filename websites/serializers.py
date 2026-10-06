from rest_framework import serializers

from .models import Website, WebsiteAPIKey


class WebsiteAPIKeySerializer(serializers.ModelSerializer):
    # `public_key` is embedded in the customer-facing widget anyway; `secret_key`
    # must never leave the server, so it is deliberately not listed.
    class Meta:
        model = WebsiteAPIKey
        fields = ("id", "key_name", "public_key", "is_active", "last_used_at", "created_at")


class WebsiteSerializer(serializers.ModelSerializer):
    api_keys = WebsiteAPIKeySerializer(many=True, read_only=True)

    class Meta:
        model = Website
        fields = (
            "id",
            "name",
            "brand_name",
            "domain",
            "source_identifier",
            "primary_color",
            "widget_enabled",
            "is_active",
            "settings",
            "api_keys",
            "created_at",
        )
