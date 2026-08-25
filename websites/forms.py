from django import forms

from core.forms import StyledFormMixin, StyledModelForm

from .models import Website, WebsiteAPIKey


class WebsiteForm(StyledModelForm):
    class Meta:
        model = Website
        fields = (
            "name",
            "brand_name",
            "domain",
            "source_identifier",
            "logo",
            "primary_color",
            "widget_enabled",
            "is_active",
            "notes",
        )
        help_texts = {
            "source_identifier": "Short slug used to tag every lead and conversation from this site.",
            "domain": "Hostname only, e.g. scaredtravel.com",
        }

    def clean_domain(self):
        domain = self.cleaned_data["domain"].strip().lower()
        for prefix in ("https://", "http://"):
            if domain.startswith(prefix):
                domain = domain[len(prefix) :]
        return domain.rstrip("/")


class WebsiteAPIKeyForm(StyledModelForm):
    class Meta:
        model = WebsiteAPIKey
        fields = ("key_name",)


class WebsiteFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False,
        choices=[("", "Any status"), ("active", "Active"), ("inactive", "Inactive")],
    )
