from rest_framework import serializers

from .models import (
    CarRental,
    Destination,
    Hotel,
    HotelOffer,
    InventoryWebsiteVisibility,
    TourPackage,
)


class DestinationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Destination
        fields = ("id", "name", "code", "city", "state", "country", "is_active")


class HotelOfferSerializer(serializers.ModelSerializer):
    class Meta:
        model = HotelOffer
        fields = (
            "id",
            "title",
            "room_type",
            "price",
            "currency",
            "valid_from",
            "valid_to",
            "inclusions",
            "is_active",
        )


class HotelSerializer(serializers.ModelSerializer):
    destination = DestinationSerializer(read_only=True)
    offers = HotelOfferSerializer(many=True, read_only=True)

    class Meta:
        model = Hotel
        fields = (
            "id",
            "name",
            "destination",
            "star_rating",
            "address",
            "description",
            "amenities",
            "base_price",
            "currency",
            "is_active",
            "offers",
        )


class CarRentalSerializer(serializers.ModelSerializer):
    destination = DestinationSerializer(read_only=True)

    class Meta:
        model = CarRental
        fields = (
            "id",
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
            "is_active",
        )


class TourPackageSerializer(serializers.ModelSerializer):
    destination = DestinationSerializer(read_only=True)

    class Meta:
        model = TourPackage
        fields = (
            "id",
            "name",
            "destination",
            "duration_days",
            "duration_nights",
            "base_price",
            "currency",
            "inclusions",
            "exclusions",
            "itinerary",
            "description",
            "is_active",
        )


class VisibilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryWebsiteVisibility
        fields = (
            "id",
            "website",
            "inventory_type",
            "object_id",
            "is_visible",
            "priority",
        )


class InventorySearchResultSerializer(serializers.Serializer):
    """Serialises the normalised dicts returned by `search_inventory`."""

    inventory_type = serializers.CharField()
    id = serializers.IntegerField()
    name = serializers.CharField()
    destination = serializers.CharField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    price_label = serializers.CharField()
    currency = serializers.CharField()
    detail = serializers.CharField()
    description = serializers.CharField()
    image = serializers.CharField()
    priority = serializers.IntegerField()
    # Hotels: the room offer behind `price` (null when it is the base price).
    offer = serializers.JSONField(required=False, allow_null=True)
