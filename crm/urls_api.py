from django.urls import path
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api import EnvelopeMixin, ErrorResponse, SuccessResponse
from core.attribution import clean_attribution
from core.cors import allow_cors_for
from core.permissions import IsSalesTeam
from core.throttling import PublicIntakeThrottle
from websites.selectors import active_key_for
from websites.services import touch_api_key

from . import selectors, services
from .forms import LeadFilterForm, WebsiteIntakeForm
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


class WebsiteIntakeAPI(APIView):
    """Public enquiry form on a client website: creates a `website_form` lead.

    Authenticated by the website's public widget key, like the chat widget.
    Accepts JSON or form-encoded fields; the reply only carries a reference
    the visitor can quote, never ids, scores or staff names.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PublicIntakeThrottle]

    def post(self, request):
        key = request.data.get("key")
        api_key = active_key_for(key) if isinstance(key, str) and len(key) <= 64 else None
        if api_key is None:
            return ErrorResponse("Unknown or inactive website key.", status_code=403)
        allow_cors_for(request, api_key.website)
        touch_api_key(api_key)

        form = WebsiteIntakeForm(request.data)
        if not form.is_valid():
            return ErrorResponse("Please check the details.", detail=form.errors)
        data = form.cleaned_data

        lead = services.create_lead_from_form(
            website=api_key.website,
            name=data["name"],
            email=data.get("email") or "",
            phone=data.get("phone") or "",
            message=data.get("message") or "",
            destination=data.get("destination") or "",
            travel_start=data.get("travel_start"),
            travel_end=data.get("travel_end"),
            travelers=data.get("travellers"),
            budget=data.get("budget"),
            attribution=clean_attribution(request.data),
            request=request,
        )
        return Response(
            {
                "success": True,
                "message": "Thank you — our travel team will be in touch shortly.",
                "lead_reference": lead.reference,
            },
            status=status.HTTP_201_CREATED,
        )


urlpatterns = [
    path("intake/", WebsiteIntakeAPI.as_view(), name="intake"),
    path("customers/", CustomerListAPI.as_view(), name="customers"),
    path("customers/<int:pk>/", CustomerDetailAPI.as_view(), name="customer_detail"),
    path("leads/", LeadListAPI.as_view(), name="leads"),
    path("leads/pipeline/", LeadPipelineAPI.as_view(), name="pipeline"),
    path("leads/<int:pk>/", LeadDetailAPI.as_view(), name="lead_detail"),
    path("follow-ups/", FollowUpListAPI.as_view(), name="follow_ups"),
]
