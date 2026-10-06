from django.urls import path
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from core.api import EnvelopeMixin, SuccessResponse
from core.permissions import IsSalesTeam

from . import selectors
from .forms import LeadFilterForm
from .serializers import CustomerSerializer, FollowUpTaskSerializer, LeadSerializer

app_name = "api_crm"


class CustomerListAPI(EnvelopeMixin, generics.ListAPIView):
    permission_classes = [IsSalesTeam]
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return selectors.list_customers(
            q=self.request.query_params.get("q", ""), user=self.request.user
        )


class CustomerDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    permission_classes = [IsSalesTeam]
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return selectors.list_customers(user=self.request.user)


class LeadListAPI(EnvelopeMixin, generics.ListAPIView):
    """Same filters as the leads page: q, status, source, destination,
    assigned_to (user id), website (id), created_from / created_to (YYYY-MM-DD)."""

    permission_classes = [IsSalesTeam]
    serializer_class = LeadSerializer

    def get_queryset(self):
        form = LeadFilterForm(self.request.query_params)
        if not form.is_valid():
            raise ValidationError(form.errors)
        data = form.cleaned_data
        return selectors.list_leads(
            q=data.get("q") or "",
            status=data.get("status") or "",
            source=data.get("source") or "",
            destination=data.get("destination") or "",
            assigned_to=data.get("assigned_to"),
            website=data.get("website"),
            created_from=data.get("created_from"),
            created_to=data.get("created_to"),
            user=self.request.user,
        )


class LeadDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    permission_classes = [IsSalesTeam]
    serializer_class = LeadSerializer

    def get_queryset(self):
        return selectors.list_leads(user=self.request.user)


class LeadPipelineAPI(APIView):
    permission_classes = [IsSalesTeam]

    def get(self, request):
        columns = selectors.pipeline_columns(user=request.user)
        return SuccessResponse(
            [
                {
                    "status": column["status"],
                    "label": column["label"],
                    "count": column["count"],
                    "leads": LeadSerializer(column["leads"], many=True).data,
                }
                for column in columns
            ]
        )


class FollowUpListAPI(EnvelopeMixin, generics.ListAPIView):
    permission_classes = [IsSalesTeam]
    serializer_class = FollowUpTaskSerializer

    def get_queryset(self):
        bucket = self.request.query_params.get("bucket", "")
        buckets = selectors.follow_up_buckets(
            user=self.request.user, include_completed=bucket == "completed"
        )
        if bucket in buckets:
            return buckets[bucket]
        return (
            selectors.visible_follow_ups(self.request.user)
            .filter(is_completed=False)
            .select_related("lead")
        )


urlpatterns = [
    path("customers/", CustomerListAPI.as_view(), name="customers"),
    path("customers/<int:pk>/", CustomerDetailAPI.as_view(), name="customer_detail"),
    path("leads/", LeadListAPI.as_view(), name="leads"),
    path("leads/pipeline/", LeadPipelineAPI.as_view(), name="pipeline"),
    path("leads/<int:pk>/", LeadDetailAPI.as_view(), name="lead_detail"),
    path("follow-ups/", FollowUpListAPI.as_view(), name="follow_ups"),
]
