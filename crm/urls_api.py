from django.urls import path
from rest_framework import generics
from rest_framework.views import APIView

from core.api import EnvelopeMixin, SuccessResponse

from . import selectors
from .serializers import CustomerSerializer, FollowUpTaskSerializer, LeadSerializer

app_name = "api_crm"


class CustomerListAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return selectors.list_customers(q=self.request.query_params.get("q", ""))


class CustomerDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return selectors.list_customers()


class LeadListAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = LeadSerializer

    def get_queryset(self):
        params = self.request.query_params
        return selectors.list_leads(
            q=params.get("q", ""),
            status=params.get("status", ""),
            source=params.get("source", ""),
            destination=params.get("destination", ""),
            user=self.request.user,
        )


class LeadDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    serializer_class = LeadSerializer

    def get_queryset(self):
        return selectors.list_leads(user=self.request.user)


class LeadPipelineAPI(APIView):
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
    serializer_class = FollowUpTaskSerializer

    def get_queryset(self):
        buckets = selectors.follow_up_buckets(user=self.request.user)
        bucket = self.request.query_params.get("bucket", "")
        if bucket in buckets:
            return buckets[bucket]
        from .models import FollowUpTask

        return FollowUpTask.objects.filter(is_completed=False).select_related("lead")


urlpatterns = [
    path("customers/", CustomerListAPI.as_view(), name="customers"),
    path("customers/<int:pk>/", CustomerDetailAPI.as_view(), name="customer_detail"),
    path("leads/", LeadListAPI.as_view(), name="leads"),
    path("leads/pipeline/", LeadPipelineAPI.as_view(), name="pipeline"),
    path("leads/<int:pk>/", LeadDetailAPI.as_view(), name="lead_detail"),
    path("follow-ups/", FollowUpListAPI.as_view(), name="follow_ups"),
]
