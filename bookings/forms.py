from django import forms

from core.forms import DateInput, StyledFormMixin, StyledModelForm

from .models import Booking, BookingStatus, PaymentStatus


class BookingForm(StyledModelForm):
    product_type = forms.ChoiceField(
        choices=[("hotel", "Hotel"), ("car", "Car Rental"), ("package", "Tour Package")]
    )
    hotel_offer = forms.ModelChoiceField(
        required=False,
        queryset=None,
        label="Room offer",
        empty_label="Cheapest offer valid for the dates",
        help_text="Hotels only: the room type / offer to book.",
    )
    email_customer = forms.BooleanField(
        required=False,
        initial=True,
        label="Email the payment link to the customer",
        help_text="Sent to the customer's email address, if they have one.",
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
            "hotel_offer",
            "currency",
            "subtotal",
        )
        widgets = {"travel_start": DateInput(), "travel_end": DateInput()}
        help_texts = {
            "product_id": "Numeric ID of the inventory item being booked.",
            "subtotal": (
                "Leave blank to price it from inventory (hotel offer or base price × nights, "
                "car × days, package × travellers). Tax is added automatically."
            ),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from crm.selectors import visible_customers, visible_leads
        from websites.selectors import list_websites

        # Only people this user may see, or the dropdowns leak every customer's name.
        self.fields["customer"].queryset = visible_customers(user)
        self.fields["lead"].queryset = visible_leads(user)
        from inventory.models import HotelOffer

        self.fields["website"].queryset = list_websites(status="active")
        self.fields["lead"].required = False
        self.fields["subtotal"].required = False
        # Filled from the inventory item in `clean` when left blank.
        self.fields["product_name"].required = False
        self.fields["hotel_offer"].queryset = HotelOffer.objects.filter(
            is_active=True, hotel__is_deleted=False
        ).select_related("hotel").order_by("hotel__name", "price")
        self.fields["hotel_offer"].label_from_instance = lambda offer: (
            f"{offer.hotel.name} (#{offer.hotel_id}) · {offer.room_type or offer.title}"
            f" · {offer.currency} {offer.price:,.0f}/night"
        )
        # Set by `clean` when the subtotal is priced from inventory.
        self.quote = None

    def clean(self):
        cleaned = super().clean()
        from inventory.selectors import get_inventory_object

        from .services import BookingError, quote_item

        product_type = cleaned.get("product_type")
        product_id = cleaned.get("product_id")
        offer = cleaned.get("hotel_offer")
        obj = None
        if product_type and product_id:
            obj = get_inventory_object(product_type, product_id)
            if obj is None:
                self.add_error(
                    "product_id", f"No {product_type} exists with ID {product_id}."
                )
            elif not cleaned.get("product_name"):
                cleaned["product_name"] = obj.name
        if offer is not None and (product_type != "hotel" or offer.hotel_id != product_id):
            self.add_error("hotel_offer", "That offer belongs to a different hotel.")
        start, end = cleaned.get("travel_start"), cleaned.get("travel_end")
        if start and end and end < start:
            self.add_error("travel_end", "The end date cannot be before the start date.")
        subtotal = cleaned.get("subtotal")
        if subtotal is not None and subtotal <= 0:
            self.add_error("subtotal", "Enter the amount to be charged.")
        if subtotal is None and obj is not None and not self.errors:
            try:
                self.quote = quote_item(
                    inventory_type=product_type,
                    item=obj,
                    travel_start=start,
                    travel_end=end,
                    travelers=cleaned.get("travelers_count") or 1,
                    offer=offer,
                )
            except BookingError as exc:
                self.add_error(None, f"{exc} Or enter the subtotal yourself.")
        elif subtotal is None and not self.errors:
            self.add_error("subtotal", "Enter the amount to be charged.")
        return cleaned

    def apply_pricing(self, booking):
        """Copy the inventory quote (or the chosen offer) onto the unsaved booking."""
        from inventory.selectors import offer_summary

        from .services import booking_summary

        if self.quote is not None:
            booking.subtotal = self.quote["subtotal"]
            booking.currency = self.quote["currency"]
            booking.travel_end = self.quote["travel_end"]
            booking.summary = booking_summary(self.quote, base=booking.summary)
        elif self.cleaned_data.get("hotel_offer") is not None:
            booking.summary = {
                **(booking.summary or {}),
                "offer": offer_summary(self.cleaned_data["hotel_offer"]),
            }
        return booking


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
