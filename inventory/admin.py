from django.contrib import admin

from .models import (
    CarRental,
    Destination,
    Hotel,
    HotelOffer,
    InventoryWebsiteVisibility,
    TourPackage,
)


class HotelOfferInline(admin.TabularInline):
    model = HotelOffer
    extra = 0


@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "country", "code", "is_active")
    list_filter = ("is_active", "country")
    search_fields = ("name", "city", "code")
    prepopulated_fields = {"code": ("name",)}


@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ("name", "destination", "star_rating", "base_price", "currency", "is_active")
    list_filter = ("is_active", "star_rating", "destination")
    search_fields = ("name", "address")
    inlines = [HotelOfferInline]


@admin.register(HotelOffer)
class HotelOfferAdmin(admin.ModelAdmin):
    list_display = ("title", "hotel", "room_type", "price", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title", "hotel__name")


@admin.register(CarRental)
class CarRentalAdmin(admin.ModelAdmin):
    list_display = ("name", "vehicle_type", "destination", "daily_price", "seats", "is_active")
    list_filter = ("is_active", "vehicle_type")
    search_fields = ("name", "brand", "model_name")


@admin.register(TourPackage)
class TourPackageAdmin(admin.ModelAdmin):
    list_display = ("name", "destination", "duration_days", "base_price", "is_active")
    list_filter = ("is_active", "destination")
    search_fields = ("name",)


@admin.register(InventoryWebsiteVisibility)
class InventoryWebsiteVisibilityAdmin(admin.ModelAdmin):
    list_display = ("website", "inventory_type", "object_id", "is_visible", "priority")
    list_filter = ("inventory_type", "is_visible", "website")
