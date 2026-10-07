from django.urls import path

from . import views

app_name = "conversations"

urlpatterns = [
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("inbox/live/", views.InboxLiveView.as_view(), name="inbox_live"),
    path("history/", views.ConversationHistoryView.as_view(), name="history"),
    path("widget/", views.WidgetPreviewView.as_view(), name="widget_preview"),
    path("ai-settings/", views.AISettingsView.as_view(), name="ai_settings"),
    path("ai-settings/test/", views.AISettingsTestView.as_view(), name="ai_settings_test"),
    path("knowledge/", views.KnowledgeListView.as_view(), name="knowledge"),
    path("knowledge/add/", views.KnowledgeCreateView.as_view(), name="knowledge_create"),
    path("knowledge/<int:pk>/edit/", views.KnowledgeUpdateView.as_view(), name="knowledge_edit"),
    path(
        "knowledge/<int:pk>/delete/", views.KnowledgeDeleteView.as_view(), name="knowledge_delete"
    ),
    path("<int:pk>/", views.ConversationDetailView.as_view(), name="detail"),
    path("<int:pk>/live/", views.ConversationLiveView.as_view(), name="live"),
    path("<int:pk>/reply/", views.ConversationReplyView.as_view(), name="reply"),
    path("<int:pk>/take-over/", views.ConversationTakeOverView.as_view(), name="take_over"),
    path("<int:pk>/resume-ai/", views.ConversationResumeAIView.as_view(), name="resume_ai"),
    path("<int:pk>/close/", views.ConversationCloseView.as_view(), name="close"),
    path("<int:pk>/assign/", views.ConversationAssignView.as_view(), name="assign"),
    path("<int:pk>/book/", views.ConversationBookView.as_view(), name="book"),
]
