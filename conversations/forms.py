from django import forms

from core.forms import DateInput, StyledFormMixin, StyledModelForm

from .models import (
    CLAUDE_MODELS,
    GEMINI_MODELS,
    MODELS_BY_PROVIDER,
    AIProvider,
    ConversationStatus,
    KnowledgeArticle,
    KnowledgeCategory,
)


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


class WidgetBookForm(forms.Form):
    """What the widget's "Book this" form sends. Prices never come from here."""

    key = forms.CharField(max_length=64)
    session = forms.CharField(max_length=64)
    recommendation_id = forms.IntegerField(min_value=1)
    name = forms.CharField(max_length=100, required=False)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=False)
    travel_start = forms.DateField()
    travel_end = forms.DateField(required=False)
    travelers = forms.IntegerField(min_value=1, max_value=50)

    def clean_phone(self):
        phone = self.cleaned_data.get("phone", "").strip()
        if not phone:
            return ""
        digits = "".join(ch for ch in phone if ch.isdigit())
        if not 10 <= len(digits) <= 13:
            raise forms.ValidationError("Enter a valid phone number.")
        return digits

    def clean(self):
        data = super().clean()
        start, end = data.get("travel_start"), data.get("travel_end")
        if start and end and end < start:
            self.add_error("travel_end", "The end date cannot be before the start date.")
        return data


class ConversationAssignForm(StyledFormMixin, forms.Form):
    assigned_to = forms.ModelChoiceField(
        queryset=None, required=False, empty_label="Unassigned", label="Assign to"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        from core.mixins import SALES_ROLES

        self.fields["assigned_to"].queryset = User.objects.filter(
            is_active=True, is_active_employee=True, role__in=SALES_ROLES
        ).order_by("username")


class StaffBookForm(StyledFormMixin, forms.Form):
    """An agent booking a recommendation on the customer's behalf."""

    recommendation_id = forms.IntegerField(widget=forms.HiddenInput)
    travel_start = forms.DateField(widget=DateInput())
    travel_end = forms.DateField(required=False, widget=DateInput())
    travelers = forms.IntegerField(min_value=1, max_value=50, initial=1)


def _mask(key):
    return f"{key[:6]}…{key[-4:]}" if len(key) > 14 else "••••"


class AISettingsForm(StyledFormMixin, forms.Form):
    """Edits the AISettings row. API keys are write-only: never sent back to the page."""

    enabled = forms.BooleanField(
        required=False,
        label="Let AI write chat replies",
        help_text="Off, or with no key, the chat uses the built-in rule-based replies.",
    )
    provider = forms.ChoiceField(choices=AIProvider.choices, label="Provider")
    model = forms.ChoiceField(
        choices=[("Claude", CLAUDE_MODELS), ("Gemini", GEMINI_MODELS)],
        label="Model",
        required=False,
    )
    custom_model = forms.CharField(
        required=False,
        max_length=100,
        label="Other model ID",
        help_text="Optional. Overrides the list, e.g. a newly released model.",
    )
    anthropic_api_key = forms.CharField(
        required=False,
        label="Anthropic API key",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
    )
    clear_anthropic_key = forms.BooleanField(required=False, label="Remove saved Anthropic key")
    gemini_api_key = forms.CharField(
        required=False,
        label="Gemini API key",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
    )
    clear_gemini_key = forms.BooleanField(required=False, label="Remove saved Gemini key")
    handoff_wait_minutes = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=1440,
        label="Resume AI if no one picks up a handoff within (minutes)",
        help_text="The customer is told a consultant is busy and the AI carries on. 0 = never.",
    )
    agent_idle_minutes = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=1440,
        label="Resume AI if the agent leaves the customer waiting for (minutes)",
        help_text="Counts from the customer's oldest unanswered message. 0 = never.",
    )

    def __init__(self, *args, instance, **kwargs):
        self.instance = instance
        listed = {value for choices in MODELS_BY_PROVIDER.values() for value, _ in choices}
        initial = {
            "enabled": instance.enabled,
            "provider": instance.provider,
            "handoff_wait_minutes": instance.handoff_wait_minutes,
            "agent_idle_minutes": instance.agent_idle_minutes,
        }
        if instance.model in listed:
            initial["model"] = instance.model
        else:
            initial["custom_model"] = instance.model
        kwargs.setdefault("initial", initial)
        super().__init__(*args, **kwargs)

        for provider, field in (
            (AIProvider.ANTHROPIC, "anthropic_api_key"),
            (AIProvider.GEMINI, "gemini_api_key"),
        ):
            saved = instance.stored_key(provider)
            self.fields[field].help_text = (
                f"Saved: {_mask(saved)}. Leave blank to keep it." if saved else "No key saved."
            )
        self.fields["anthropic_api_key"].widget.attrs["placeholder"] = "sk-ant-…"
        self.fields["gemini_api_key"].widget.attrs["placeholder"] = "AIza…"

    def clean(self):
        data = super().clean()
        provider = data.get("provider")
        model = (data.get("custom_model") or "").strip() or data.get("model")
        if not model:
            self.add_error("model", "Pick a model or type a model ID.")
        elif not data.get("custom_model") and provider:
            allowed = {value for value, _ in MODELS_BY_PROVIDER[provider]}
            if model not in allowed:
                self.add_error(
                    "model",
                    f"{model} is not a {AIProvider(provider).label} model — pick one from that group.",
                )
        data["resolved_model"] = model
        return data

    def save(self, user=None):
        """Apply the form to the settings row. Returns the names of keys that changed."""
        data = self.cleaned_data
        obj = self.instance
        obj.enabled = data["enabled"]
        obj.provider = data["provider"]
        obj.model = data["resolved_model"]
        for field_name in ("handoff_wait_minutes", "agent_idle_minutes"):
            if data.get(field_name) is not None:
                setattr(obj, field_name, data[field_name])
        changed = []
        for field, clear in (
            ("anthropic_api_key", "clear_anthropic_key"),
            ("gemini_api_key", "clear_gemini_key"),
        ):
            new = (data.get(field) or "").strip()
            if new:
                setattr(obj, field, new)
                changed.append(field)
            elif data.get(clear) and getattr(obj, field):
                setattr(obj, field, "")
                changed.append(field)
        if user is not None and user.is_authenticated:
            obj.updated_by = user
        obj.save()
        return changed


class KnowledgeArticleForm(StyledModelForm):
    class Meta:
        model = KnowledgeArticle
        fields = ("title", "category", "website", "keywords", "content", "is_active")
        widgets = {"content": forms.Textarea(attrs={"rows": 10})}
        help_texts = {
            "content": "Write it as you would tell a customer. The AI quotes only what is "
            "written here, so include the exact fees, deadlines and conditions.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from websites.selectors import list_websites

        self.fields["website"].queryset = list_websites()
        self.fields["website"].empty_label = "All websites"


class KnowledgeFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    category = forms.ChoiceField(
        required=False, choices=[("", "All categories")] + list(KnowledgeCategory.choices)
    )
    website = forms.ModelChoiceField(required=False, queryset=None, empty_label="All websites")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from websites.selectors import list_websites

        self.fields["website"].queryset = list_websites()
