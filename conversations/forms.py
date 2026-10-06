from django import forms

from core.forms import StyledFormMixin

from .models import (
    CLAUDE_MODELS,
    GEMINI_MODELS,
    MODELS_BY_PROVIDER,
    AIProvider,
    ConversationStatus,
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

    def __init__(self, *args, instance, **kwargs):
        self.instance = instance
        listed = {value for choices in MODELS_BY_PROVIDER.values() for value, _ in choices}
        initial = {"enabled": instance.enabled, "provider": instance.provider}
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
