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
