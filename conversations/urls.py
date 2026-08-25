from django.urls import path

from . import views

app_name = "conversations"

urlpatterns = [
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("history/", views.ConversationHistoryView.as_view(), name="history"),
    path("widget/", views.WidgetPreviewView.as_view(), name="widget_preview"),
    path("<int:pk>/", views.ConversationDetailView.as_view(), name="detail"),
    path("<int:pk>/reply/", views.ConversationReplyView.as_view(), name="reply"),
    path("<int:pk>/take-over/", views.ConversationTakeOverView.as_view(), name="take_over"),
    path("<int:pk>/resume-ai/", views.ConversationResumeAIView.as_view(), name="resume_ai"),
    path("<int:pk>/close/", views.ConversationCloseView.as_view(), name="close"),
]
