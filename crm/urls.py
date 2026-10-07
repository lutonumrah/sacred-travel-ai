from django.urls import path

from . import views

app_name = "crm"

urlpatterns = [
    path("customers/", views.CustomerListView.as_view(), name="customers"),
    path("customers/add/", views.CustomerCreateView.as_view(), name="customer_create"),
    path("customers/<int:pk>/", views.CustomerDetailView.as_view(), name="customer_detail"),
    path("customers/<int:pk>/edit/", views.CustomerUpdateView.as_view(), name="customer_edit"),
    path(
        "customers/<int:pk>/archive/", views.CustomerArchiveView.as_view(), name="customer_archive"
    ),
    path(
        "customers/<int:pk>/restore/",
        views.CustomerArchiveView.as_view(restore=True),
        name="customer_restore",
    ),
    path("leads/", views.LeadListView.as_view(), name="leads"),
    path("leads/pipeline/", views.LeadPipelineView.as_view(), name="pipeline"),
    path("leads/add/", views.LeadCreateView.as_view(), name="lead_create"),
    path("leads/<int:pk>/", views.LeadDetailView.as_view(), name="lead_detail"),
    path("leads/<int:pk>/edit/", views.LeadUpdateView.as_view(), name="lead_edit"),
    path("leads/<int:pk>/status/", views.LeadStatusUpdateView.as_view(), name="lead_status"),
    path("leads/<int:pk>/move/", views.LeadMoveView.as_view(), name="lead_move"),
    path("leads/<int:pk>/assign/", views.LeadAssignView.as_view(), name="lead_assign"),
    path("leads/<int:pk>/notes/", views.LeadNoteCreateView.as_view(), name="lead_note"),
    path("follow-ups/", views.FollowUpListView.as_view(), name="follow_ups"),
    path("follow-ups/add/", views.FollowUpCreateView.as_view(), name="follow_up_create"),
    path("follow-ups/<int:pk>/edit/", views.FollowUpUpdateView.as_view(), name="follow_up_edit"),
    path(
        "follow-ups/<int:pk>/complete/",
        views.FollowUpCompleteView.as_view(),
        name="follow_up_complete",
    ),
]
