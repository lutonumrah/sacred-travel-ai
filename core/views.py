from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from core.api import SuccessResponse


class HealthCheckAPIView(APIView):
    """Unauthenticated liveness probe."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.db import connection

        try:
            connection.ensure_connection()
            database = "ok"
        except Exception:
            database = "unavailable"
        return SuccessResponse(
            {"status": "ok", "service": "scared-travel-ai", "database": database}
        )
