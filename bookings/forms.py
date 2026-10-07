from django import forms

from core.forms import DateInput, StyledFormMixin, StyledModelForm

from .models import Booking, BookingStatus, PaymentStatus


class BookingForm(StyledModelForm):
    product_type = forms.ChoiceField(
        choices=[("hotel", "Hotel"), ("car", "Car Rental"), ("package", "Tour Package")]
    )

    class Meta:
        model = Booking
        fields = (
            "customer",
            "website",
            "lead",
            "product_type",
            "product_id",
            "product_name",
            "travel_start",
            "travel_end",
            "travelers_count",
            "currency",
            "subtotal",
        )
        widgets = {"travel_start": DateInput(), "travel_end": DateInput()}
        help_texts = {
            "product_id": "Numeric ID of the inventory item being booked.",
            "subtotal": "Tax is added automatically from BOOKING_TAX_PERCENT.",
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from crm.selectors import visible_customers, visible_leads
        from websites.selectors import list_websites

        # Only people this user may see, or the dropdowns leak every customer's name.
        self.fields["customer"].queryset = visible_customers(user)
        self.fields["lead"].queryset = visible_leads(user)
        self.fields["website"].queryset = list_websites(status="active")
        self.fields["lead"].required = False

    def clean(self):
        cleaned = super().clean()
        from inventory.selectors import get_inventory_object

        product_type = cleaned.get("product_type")
        product_id = cleaned.get("product_id")
        if product_type and product_id:
            obj = get_inventory_object(product_type, product_id)
            if obj is None:
                self.add_error(
                    "product_id", f"No {product_type} exists with ID {product_id}."
                )
            elif not cleaned.get("product_name"):
                cleaned["product_name"] = obj.name
        start, end = cleaned.get("travel_start"), cleaned.get("travel_end")
        if start and end and end < start:
            self.add_error("travel_end", "The end date cannot be before the start date.")
        if cleaned.get("subtotal") is not None and cleaned["subtotal"] <= 0:
            self.add_error("subtotal", "Enter the amount to be charged.")
        return cleaned


class BookingFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False, choices=[("", "All statuses")] + list(BookingStatus.choices)
    )
    website = forms.ModelChoiceField(required=False, queryset=None, empty_label="All websites")
    date_from = forms.DateField(required=False, widget=DateInput())
    date_to = forms.DateField(required=False, widget=DateInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from websites.selectors import list_websites

        self.fields["website"].queryset = list_websites()


class PaymentFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    status = forms.ChoiceField(
        required=False, choices=[("", "All statuses")] + list(PaymentStatus.choices)
    )
