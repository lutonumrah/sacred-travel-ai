from django.db import models

from core.models import SoftDeleteModel, TimeStampedModel
from websites.models import Website


class InventoryType(models.TextChoices):
    HOTEL = "hotel", "Hotel"
    CAR = "car", "Car Rental"
    PACKAGE = "package", "Tour Package"


class Destination(TimeStampedModel, SoftDeleteModel):
    """Master destination list shared across inventory."""

    name = models.CharField(max_length=150)
    country = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    code = models.SlugField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Hotel(TimeStampedModel, SoftDeleteModel):
    name = models.CharField(max_length=200)
    destination = models.ForeignKey(
        Destination,
        on_delete=models.PROTECT,
        related_name="hotels",
        null=True,
        blank=True,
    )
    star_rating = models.PositiveSmallIntegerField(default=3)
    address = models.TextField(blank=True)
    description = models.TextField(blank=True)
    amenities = models.JSONField(default=list, blank=True)
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    base_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="INR")
    image = models.ImageField(upload_to="inventory/hotels/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class HotelOffer(TimeStampedModel):
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="offers")
    title = models.CharField(max_length=200)
    room_type = models.CharField(max_length=100, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    inclusions = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return f"{self.hotel.name} · {self.title}"


class CarRental(TimeStampedModel, SoftDeleteModel):
    name = models.CharField(max_length=200)
    destination = models.ForeignKey(
        Destination,
        on_delete=models.PROTECT,
        related_name="cars",
        null=True,
        blank=True,
    )
    vehicle_type = models.CharField(max_length=100)
    brand = models.CharField(max_length=100, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    seats = models.PositiveSmallIntegerField(default=4)
    transmission = models.CharField(max_length=50, blank=True)
    fuel_type = models.CharField(max_length=50, blank=True)
    daily_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="INR")
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="inventory/cars/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Car Rental"
        verbose_name_plural = "Car Rentals"

    def __str__(self):
        return self.name


class TourPackage(TimeStampedModel, SoftDeleteModel):
    name = models.CharField(max_length=200)
    destination = models.ForeignKey(
        Destination,
        on_delete=models.PROTECT,
        related_name="packages",
        null=True,
        blank=True,
    )
    duration_days = models.PositiveSmallIntegerField(default=1)
    duration_nights = models.PositiveSmallIntegerField(default=0)
    base_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="INR")
    inclusions = models.TextField(blank=True)
    exclusions = models.TextField(blank=True)
    itinerary = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="inventory/packages/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Tour Package"
        verbose_name_plural = "Tour Packages"

    def __str__(self):
        return self.name


class InventoryWebsiteVisibility(TimeStampedModel):
    """Control which inventory items appear on which websites."""

    website = models.ForeignKey(
        Website,
        on_delete=models.CASCADE,
        related_name="inventory_visibility",
    )
    inventory_type = models.CharField(max_length=20, choices=InventoryType.choices)
    object_id = models.PositiveIntegerField()
    is_visible = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-priority", "-created_at"]
        unique_together = ("website", "inventory_type", "object_id")
        verbose_name = "Inventory Website Visibility"
        verbose_name_plural = "Inventory Website Visibilities"

    def __str__(self):
        return f"{self.website.source_identifier} · {self.inventory_type}:{self.object_id}"
