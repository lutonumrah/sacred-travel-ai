from django import forms

from core.forms import StyledFormMixin

from .models import ConversationStatus


class AgentReplyForm(StyledFormMixin, forms.Form):
    content = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Reply to the customer…"}),
        label="",
    )


class HandoffForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(required=False, label="Reason")


class InboxFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All statuses")] + list(ConversationStatus.choices),
    )
    website = forms.ModelChoiceField(required=False, queryset=None, empty_label="All websites")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from websites.selectors import list_websites

        self.fields["website"].queryset = list_websites()


class WidgetChatForm(forms.Form):
    """Validates the public widget payload."""

    key = forms.CharField(max_length=64)
    message = forms.CharField(max_length=2000)
    session = forms.CharField(max_length=64, required=False)
    name = forms.CharField(max_length=100, required=False)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=False)
