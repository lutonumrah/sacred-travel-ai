from rest_framework import serializers

from .models import Team, User


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "full_name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "role",
            "is_active",
            "is_active_employee",
        )


class TeamSerializer(serializers.ModelSerializer):
    members = UserSerializer(many=True, read_only=True)

    class Meta:
        model = Team
        fields = ("id", "name", "description", "is_active", "members", "created_at")
