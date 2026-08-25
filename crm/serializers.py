from rest_framework import serializers

from .models import Customer, FollowUpTask, Lead, LeadActivity, LeadNote


class CustomerSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Customer
        fields = (
            "id",
            "full_name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "whatsapp",
            "city",
            "country",
            "preferred_language",
            "is_active",
            "created_at",
        )


class LeadNoteSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source="author.username", read_only=True, default=None)

    class Meta:
        model = LeadNote
        fields = ("id", "body", "is_internal", "author", "created_at")


class LeadActivitySerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source="actor.username", read_only=True, default=None)

    class Meta:
        model = LeadActivity
        fields = ("id", "activity_type", "summary", "details", "actor", "created_at")


class LeadSerializer(serializers.ModelSerializer):
    customer = CustomerSerializer(read_only=True)
    assigned_to = serializers.CharField(
        source="assigned_to.username", read_only=True, default=None
    )
    website = serializers.CharField(
        source="website.source_identifier", read_only=True, default=None
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Lead
        fields = (
            "id",
            "title",
            "customer",
            "website",
            "status",
            "status_display",
            "source",
            "destination",
            "travel_start",
            "travel_end",
            "travelers_count",
            "budget_min",
            "budget_max",
            "preferences",
            "assigned_to",
            "score",
            "lost_reason",
            "converted_at",
            "created_at",
        )


class FollowUpTaskSerializer(serializers.ModelSerializer):
    lead_title = serializers.CharField(source="lead.title", read_only=True)
    assigned_to = serializers.CharField(
        source="assigned_to.username", read_only=True, default=None
    )

    class Meta:
        model = FollowUpTask
        fields = (
            "id",
            "lead",
            "lead_title",
            "assigned_to",
            "title",
            "due_at",
            "reminder_at",
            "is_completed",
            "completed_at",
            "notes",
        )
