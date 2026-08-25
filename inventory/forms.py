from django import forms

from core.forms import StyledFormMixin, StyledModelForm, TimeInput

from .models import (
    CarRental,
    Destination,
    Hotel,
    HotelOffer,
    InventoryType,
    InventoryWebsiteVisibility,
    TourPackage,
)


class CommaSeparatedJSONField(forms.CharField):
    """Edits a JSON list field as a comma-separated line of text."""

    widget = forms.TextInput

    def prepare_value(self, value):
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        return value

    def clean(self, value):
        value = super().clean(value)
        return [part.strip() for part in (value or "").split(",") if part.strip()]


class DestinationForm(StyledModelForm):
    class Meta:
        model = Destination
        fields = ("name", "code", "city", "state", "country", "is_active")


class HotelForm(StyledModelForm):
    amenities = CommaSeparatedJSONField(
        required=False,
        help_text="Comma separated, e.g. Wi-Fi, Pool, Breakfast",
    )

    class Meta:
        model = Hotel
        fields = (
            "name",
            "destination",
            "star_rating",
            "address",
            "description",
            "amenities",
            "check_in_time",
            "check_out_time",
            "base_price",
            "currency",
            "image",
            "is_active",
        )
        widgets = {
            "check_in_time": TimeInput(),
            "check_out_time": TimeInput(),
        }


class HotelOfferForm(StyledModelForm):
    class Meta:
        model = HotelOffer
        fields = (
            "title",
            "room_type",
            "price",
            "currency",
            "valid_from",
            "valid_to",
            "inclusions",
            "is_active",
        )
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_to": forms.DateInput(attrs={"type": "date"}),
        }


class CarRentalForm(StyledModelForm):
    class Meta:
        model = CarRental
        fields = (
            "name",
            "destination",
            "vehicle_type",
            "brand",
            "model_name",
            "seats",
            "transmission",
            "fuel_type",
            "daily_price",
            "currency",
            "description",
            "image",
            "is_active",
        )


class TourPackageForm(StyledModelForm):
    itinerary_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        label="Itinerary",
        help_text="One line per day. `Day 1: Arrival` — the day number is added automatically.",
    )

    class Meta:
        model = TourPackage
        fields = (
            "name",
            "destination",
            "duration_days",
            "duration_nights",
            "base_price",
            "currency",
            "inclusions",
            "exclusions",
            "description",
            "image",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["itinerary_text"].initial = "\n".join(
                step.get("title", "") if isinstance(step, dict) else str(step)
                for step in (self.instance.itinerary or [])
            )

    def save(self, commit=True):
        package = super().save(commit=False)
        lines = [
            line.strip()
            for line in (self.cleaned_data.get("itinerary_text") or "").splitlines()
            if line.strip()
        ]
        package.itinerary = [
            {"day": index, "title": line} for index, line in enumerate(lines, start=1)
        ]
        if commit:
            package.save()
        return package


class InventoryFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search")
    destination = forms.ModelChoiceField(
        required=False, queryset=Destination.objects.filter(is_active=True), empty_label="All destinations"
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "Any status"), ("active", "Active"), ("inactive", "Inactive")],
    )
    min_price = forms.DecimalField(required=False, label="Min price")
    max_price = forms.DecimalField(required=False, label="Max price")


class InventorySearchForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Keyword")
    inventory_type = forms.ChoiceField(
        required=False,
        label="Type",
        choices=[("", "All types")] + list(InventoryType.choices),
    )
    destination = forms.CharField(required=False, help_text="Destination or city name")
    min_price = forms.DecimalField(required=False)
    max_price = forms.DecimalField(required=False)
    website = forms.ModelChoiceField(
        required=False, queryset=None, empty_label="All websites"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from websites.selectors import list_websites

        self.fields["website"].queryset = list_websites(status="active")


class VisibilityForm(StyledModelForm):
    class Meta:
        model = InventoryWebsiteVisibility
        fields = ("website", "inventory_type", "object_id", "is_visible", "priority")
        help_texts = {
            "object_id": "Numeric ID of the hotel, car or package.",
            "priority": "Higher values are recommended first.",
        }

    def clean(self):
        cleaned = super().clean()
        inventory_type = cleaned.get("inventory_type")
        object_id = cleaned.get("object_id")
        if inventory_type and object_id:
            model = {
                InventoryType.HOTEL: Hotel,
                InventoryType.CAR: CarRental,
                InventoryType.PACKAGE: TourPackage,
            }[inventory_type]
            if not model.objects.filter(pk=object_id, is_deleted=False).exists():
                raise forms.ValidationError(
                    f"No {inventory_type} exists with ID {object_id}."
                )
        return cleaned
