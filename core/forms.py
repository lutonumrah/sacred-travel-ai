"""Form helpers that give every module the same input styling."""

from django import forms

TEXT_WIDGETS = (
    forms.TextInput,
    forms.EmailInput,
    forms.NumberInput,
    forms.URLInput,
    forms.PasswordInput,
    forms.Textarea,
    forms.DateInput,
    forms.DateTimeInput,
    forms.TimeInput,
)


class StyledFormMixin:
    """Applies consistent CSS classes and HTML5 input types to every field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "check")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "input")
            elif isinstance(widget, TEXT_WIDGETS):
                widget.attrs.setdefault("class", "input")
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 3)


class StyledForm(StyledFormMixin, forms.Form):
    pass


class StyledModelForm(StyledFormMixin, forms.ModelForm):
    pass


class DateInput(forms.DateInput):
    input_type = "date"


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def format_value(self, value):
        value = super().format_value(value)
        # `datetime-local` rejects the seconds-and-space format Django emits.
        if value and " " in value:
            value = value.replace(" ", "T")
        return value[:16] if value else value


class TimeInput(forms.TimeInput):
    input_type = "time"
