from django import forms

from core.forms import DateInput, StyledFormMixin


class ReportFilterForm(StyledFormMixin, forms.Form):
    """Website and date range for reports, analytics and the CSV exports.

    `days` is read separately (see `views._days`); an explicit From / To wins over it.
    """

    website = forms.ModelChoiceField(required=False, queryset=None, empty_label="All websites")
    date_from = forms.DateField(required=False, widget=DateInput(), label="From")
    date_to = forms.DateField(required=False, widget=DateInput(), label="To")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from websites.selectors import list_websites

        self.fields["website"].queryset = list_websites()
