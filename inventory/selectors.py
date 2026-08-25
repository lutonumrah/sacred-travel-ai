"""Inventory queries, including the unified search shared by the AI engine."""

from decimal import Decimal, InvalidOperation

from django.db.models import Q

from .models import (
    CarRental,
    Destination,
    Hotel,
    InventoryType,
    InventoryWebsiteVisibility,
    TourPackage,
)

MODEL_BY_TYPE = {
    InventoryType.HOTEL: Hotel,
    InventoryType.CAR: CarRental,
    InventoryType.PACKAGE: TourPackage,
}

PRICE_FIELD_BY_TYPE = {
    InventoryType.HOTEL: "base_price",
    InventoryType.CAR: "daily_price",
    InventoryType.PACKAGE: "base_price",
}


def _to_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def list_destinations(*, q="", status=""):
    queryset = Destination.objects.all()
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q)
            | Q(city__icontains=q)
            | Q(state__icontains=q)
            | Q(country__icontains=q)
            | Q(code__icontains=q)
        )
    if status == "active":
        queryset = queryset.filter(is_active=True)
    elif status == "inactive":
        queryset = queryset.filter(is_active=False)
    return queryset


def _base_filter(queryset, *, q, destination, status, min_price, max_price, price_field, search_fields):
    queryset = queryset.filter(is_deleted=False)
    if q:
        condition = Q()
        for field in search_fields:
            condition |= Q(**{f"{field}__icontains": q})
        queryset = queryset.filter(condition)
    if destination:
        queryset = queryset.filter(destination=destination)
    if status == "active":
        queryset = queryset.filter(is_active=True)
    elif status == "inactive":
        queryset = queryset.filter(is_active=False)
    min_price = _to_decimal(min_price)
    max_price = _to_decimal(max_price)
    if min_price is not None:
        queryset = queryset.filter(**{f"{price_field}__gte": min_price})
    if max_price is not None:
        queryset = queryset.filter(**{f"{price_field}__lte": max_price})
    return queryset.select_related("destination")


def list_hotels(*, q="", destination=None, status="", min_price=None, max_price=None):
    return _base_filter(
        Hotel.objects.all(),
        q=q,
        destination=destination,
        status=status,
        min_price=min_price,
        max_price=max_price,
        price_field="base_price",
        search_fields=("name", "address", "description", "destination__name"),
    )


def list_cars(*, q="", destination=None, status="", min_price=None, max_price=None):
    return _base_filter(
        CarRental.objects.all(),
        q=q,
        destination=destination,
        status=status,
        min_price=min_price,
        max_price=max_price,
        price_field="daily_price",
        search_fields=("name", "brand", "model_name", "vehicle_type", "destination__name"),
    )


def list_packages(*, q="", destination=None, status="", min_price=None, max_price=None):
    return _base_filter(
        TourPackage.objects.all(),
        q=q,
        destination=destination,
        status=status,
        min_price=min_price,
        max_price=max_price,
        price_field="base_price",
        search_fields=("name", "description", "inclusions", "destination__name"),
    )


def list_visibility(*, website=None, inventory_type=""):
    queryset = InventoryWebsiteVisibility.objects.select_related("website")
    if website:
        queryset = queryset.filter(website=website)
    if inventory_type:
        queryset = queryset.filter(inventory_type=inventory_type)
    return queryset


def hidden_ids_for_website(website, inventory_type):
    """IDs explicitly hidden for a website.

    Inventory is visible everywhere by default; a visibility row with
    `is_visible=False` is what takes an item off a specific website.
    """
    if website is None:
        return set()
    return set(
        InventoryWebsiteVisibility.objects.filter(
            website=website, inventory_type=inventory_type, is_visible=False
        ).values_list("object_id", flat=True)
    )


def priority_map_for_website(website, inventory_type):
    if website is None:
        return {}
    rows = InventoryWebsiteVisibility.objects.filter(
        website=website, inventory_type=inventory_type, is_visible=True
    ).values_list("object_id", "priority")
    return {object_id: priority for object_id, priority in rows}


def serialize_item(obj, inventory_type):
    """Normalise the three inventory models into one shape."""
    common = {
        "inventory_type": inventory_type,
        "id": obj.pk,
        "name": obj.name,
        "destination": obj.destination.name if obj.destination_id else "",
        "currency": obj.currency,
        "image": obj.image.url if obj.image else "",
        "is_active": obj.is_active,
    }
    if inventory_type == InventoryType.HOTEL:
        common.update(
            {
                "price": obj.base_price,
                "price_label": "per night",
                "detail": f"{obj.star_rating}-star",
                "description": obj.description,
                "amenities": obj.amenities or [],
            }
        )
    elif inventory_type == InventoryType.CAR:
        common.update(
            {
                "price": obj.daily_price,
                "price_label": "per day",
                "detail": f"{obj.vehicle_type} · {obj.seats} seats",
                "description": obj.description,
                "amenities": [],
            }
        )
    else:
        common.update(
            {
                "price": obj.base_price,
                "price_label": "per person",
                "detail": f"{obj.duration_days}D/{obj.duration_nights}N",
                "description": obj.description,
                "amenities": [],
            }
        )
    return common


def search_inventory(
    *,
    q="",
    inventory_type="",
    destination="",
    min_price=None,
    max_price=None,
    website=None,
    limit=30,
    active_only=True,
):
    """Unified search across hotels, cars and packages.

    Returns normalised dicts sorted by website priority, then by price.
    Used by the inventory search page, the public API and the AI engine.
    """
    wanted = [inventory_type] if inventory_type else list(MODEL_BY_TYPE)
    status = "active" if active_only else ""
    results = []

    listers = {
        InventoryType.HOTEL: list_hotels,
        InventoryType.CAR: list_cars,
        InventoryType.PACKAGE: list_packages,
    }

    for kind in wanted:
        if kind not in listers:
            continue
        queryset = listers[kind](
            q=q, status=status, min_price=min_price, max_price=max_price
        )
        if destination:
            queryset = queryset.filter(
                Q(destination__name__icontains=destination)
                | Q(destination__city__icontains=destination)
                | Q(destination__code__icontains=destination)
                | Q(name__icontains=destination)
            )
        hidden = hidden_ids_for_website(website, kind)
        priorities = priority_map_for_website(website, kind)
        for obj in queryset[: limit * 2]:
            if obj.pk in hidden:
                continue
            item = serialize_item(obj, kind)
            item["priority"] = priorities.get(obj.pk, 0)
            results.append(item)

    results.sort(key=lambda item: (-item["priority"], item["price"] or 0))
    return results[:limit]


def get_inventory_object(inventory_type, object_id):
    model = MODEL_BY_TYPE.get(inventory_type)
    if not model:
        return None
    return model.objects.filter(pk=object_id, is_deleted=False).first()
