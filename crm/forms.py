from django import forms

from core.forms import DateInput, DateTimeInput, StyledFormMixin, StyledModelForm

from .models import Customer, FollowUpTask, Lead, LeadNote, LeadSource, LeadStatus


class CustomerForm(StyledModelForm):
    class Meta:
        model = Customer
        fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "whatsapp",
            "city",
            "country",
            "preferred_language",
            "notes",
            "is_active",
        )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("email") and not cleaned.get("phone"):
            raise forms.ValidationError("Provide at least an email address or a phone number.")
        return cleaned


class CustomerFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    city = forms.CharField(required=False)


class LeadForm(StyledModelForm):
    class Meta:
        model = Lead
        fields = (
            "title",
            "customer",
            "website",
            "status",
            "source",
            "destination",
            "travel_start",
            "travel_end",
            "travelers_count",
            "budget_min",
            "budget_max",
            "assigned_to",
            "assigned_team",
        )
        widgets = {
            "travel_start": DateInput(),
            "travel_end": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.selectors import assignable_users
        from websites.selectors import list_websites

        self.fields["customer"].queryset = Customer.objects.filter(is_deleted=False)
        self.fields["assigned_to"].queryset = assignable_users()
        self.fields["website"].queryset = list_websites(status="active")

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("travel_start"), cleaned.get("travel_end")
        if start and end and end < start:
            self.add_error("travel_end", "Return date cannot be before the departure date.")
        low, high = cleaned.get("budget_min"), cleaned.get("budget_max")
        if low is not None and high is not None and high < low:
            self.add_error("budget_max", "Maximum budget cannot be below the minimum.")
        return cleaned


class LeadFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False, choices=[("", "All statuses")] + list(LeadStatus.choices)
    )
    source = forms.ChoiceField(
        required=False, choices=[("", "All sources")] + list(LeadSource.choices)
    )
    destination = forms.CharField(required=False)
    assigned_to = forms.ModelChoiceField(
        required=False, queryset=None, empty_label="Anyone"
    )
    website = forms.ModelChoiceField(required=False, queryset=None, empty_label="All websites")
    created_from = forms.DateField(required=False, widget=DateInput())
    created_to = forms.DateField(required=False, widget=DateInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.selectors import assignable_users
        from websites.selectors import list_websites

        self.fields["assigned_to"].queryset = assignable_users()
        self.fields["website"].queryset = list_websites()


class LeadNoteForm(StyledModelForm):
    class Meta:
        model = LeadNote
        fields = ("body", "is_internal")
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "placeholder": "Add a note…"})}


class LeadStatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=LeadStatus.choices)
    lost_reason = forms.CharField(required=False)


class LeadAssignForm(StyledFormMixin, forms.Form):
    assigned_to = forms.ModelChoiceField(
        required=False, queryset=None, empty_label="Unassigned"
    )
    assigned_team = forms.ModelChoiceField(
        required=False, queryset=None, empty_label="No team"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import Team
        from accounts.selectors import assignable_users

        self.fields["assigned_to"].queryset = assignable_users()
        self.fields["assigned_team"].queryset = Team.objects.filter(is_active=True)


class FollowUpTaskForm(StyledModelForm):
    class Meta:
        model = FollowUpTask
        fields = ("lead", "assigned_to", "title", "due_at", "reminder_at", "notes")
        widgets = {"due_at": DateTimeInput(), "reminder_at": DateTimeInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.selectors import assignable_users

        self.fields["lead"].queryset = Lead.objects.filter(is_deleted=False)
        self.fields["assigned_to"].queryset = assignable_users()

    def clean(self):
        cleaned = super().clean()
        due, reminder = cleaned.get("due_at"), cleaned.get("reminder_at")
        if due and reminder and reminder > due:
            self.add_error("reminder_at", "The reminder must fire on or before the due date.")
        return cleaned


class WebsiteIntakeForm(forms.Form):
    """The public website enquiry form (`POST /api/v1/crm/intake/`)."""

    key = forms.CharField(max_length=64)
    name = forms.CharField(max_length=100)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=False)
    message = forms.CharField(max_length=2000, required=False)
    destination = forms.CharField(max_length=150, required=False)
    travel_start = forms.DateField(required=False)
    travel_end = forms.DateField(required=False)
    travellers = forms.IntegerField(min_value=1, max_value=50, required=False)
    budget = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, required=False)

    def __init__(self, data=None, *args, **kwargs):
        # Accept the American spelling too; host sites name fields their own way.
        if data is not None and not data.get("travellers") and data.get("travelers"):
            data = data.copy()
            data["travellers"] = data.get("travelers")
        super().__init__(data, *args, **kwargs)

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
        if not data.get("email") and not data.get("phone") and not self.has_error("phone"):
            raise forms.ValidationError("Please give an email address or a phone number.")
        start, end = data.get("travel_start"), data.get("travel_end")
        if start and end and end < start:
            self.add_error("travel_end", "The return date cannot be before the departure date.")
        return data


class FollowUpEditForm(StyledModelForm):
    """Edit an existing follow-up; the lead it belongs to does not change."""

    class Meta:
        model = FollowUpTask
        fields = ("title", "assigned_to", "due_at", "reminder_at", "notes")
        widgets = {"due_at": DateTimeInput(), "reminder_at": DateTimeInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.selectors import assignable_users

        self.fields["assigned_to"].queryset = assignable_users()

    def clean(self):
        cleaned = super().clean()
        due, reminder = cleaned.get("due_at"), cleaned.get("reminder_at")
        if due and reminder and reminder > due:
            self.add_error("reminder_at", "The reminder must fire on or before the due date.")
        return cleaned
