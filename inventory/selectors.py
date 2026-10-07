"""Inventory queries, including the unified search shared by the AI engine."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    CarRental,
    Destination,
    Hotel,
    HotelOffer,
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


# --------------------------------------------------------------------------
# Hotel nightly price
# --------------------------------------------------------------------------
#
# The one rule: a hotel's nightly price is its cheapest active room offer valid
# for every night of the stay (today when no dates are known); with no such
# offer it is the hotel's base price. Search, the AI, max-price filters and
# booking quotes all go through `offers_valid_for`, so they cannot disagree.


def _as_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def stay_nights(check_in=None, check_out=None):
    """`(first_night, last_night)` for a stay; today for both when no dates are known."""
    first = _as_date(check_in) or timezone.localdate()
    out = _as_date(check_out)
    last = out - timedelta(days=1) if out and out > first else first
    return first, last


def offers_valid_for(check_in=None, check_out=None):
    """Active offers whose validity window covers every night of the stay."""
    first, last = stay_nights(check_in, check_out)
    return HotelOffer.objects.filter(is_active=True).filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=first),
        Q(valid_to__isnull=True) | Q(valid_to__gte=last),
    )


def best_offer(hotel, check_in=None, check_out=None):
    """The cheapest offer that applies, or None (the base price applies)."""
    return (
        offers_valid_for(check_in, check_out).filter(hotel=hotel).order_by("price", "pk").first()
    )


def hotel_nightly_price(hotel, check_in=None, check_out=None, offer=None):
    """`(price, currency, offer)` for one night; `offer` forces a specific offer."""
    offer = offer or best_offer(hotel, check_in, check_out)
    if offer is not None:
        return offer.price, offer.currency, offer
    return hotel.base_price, hotel.currency, None


def with_nightly_price(queryset, check_in=None, check_out=None):
    """Annotate hotels with `nightly_price` (the rule above, in SQL for filtering)."""
    cheapest = (
        offers_valid_for(check_in, check_out)
        .filter(hotel=OuterRef("pk"))
        .order_by("price", "pk")
        .values("price")[:1]
    )
    return queryset.annotate(nightly_price=Coalesce(Subquery(cheapest), F("base_price")))


def offer_summary(offer):
    """What a booking or recommendation keeps of the offer it was priced with."""
    if offer is None:
        return None
    return {
        "id": offer.pk,
        "title": offer.title,
        "room_type": offer.room_type,
        "inclusions": offer.inclusions,
        "price": str(offer.price),
        "currency": offer.currency,
    }


def list_destinations(*, q="", status="", include_archived=False):
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


def _base_filter(
    queryset,
    *,
    q,
    destination,
    status,
    min_price,
    max_price,
    price_field,
    search_fields,
    include_archived=False,
):
    if not include_archived and status != "archived":
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


def list_hotels(
    *,
    q="",
    destination=None,
    status="",
    min_price=None,
    max_price=None,
    include_archived=False,
    check_in=None,
    check_out=None,
):
    """Hotels, price-filtered on the nightly price for the dates (see `with_nightly_price`)."""
    return _base_filter(
        with_nightly_price(Hotel.objects.all(), check_in, check_out),
        q=q,
        destination=destination,
        status=status,
        min_price=min_price,
        max_price=max_price,
        price_field="nightly_price",
        search_fields=("name", "address", "description", "destination__name"),
        include_archived=include_archived,
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


def serialize_item(obj, inventory_type, *, check_in=None, check_out=None):
    """Normalise the three inventory models into one shape.

    A hotel is priced per `hotel_nightly_price` for the given dates; `offer` names
    the room offer behind that price (None when it is the base price).
    """
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
        price, currency, offer = hotel_nightly_price(obj, check_in, check_out)
        room = (offer.room_type or offer.title) if offer else ""
        common.update(
            {
                "price": price,
                "currency": currency,
                "price_label": "per night",
                "detail": f"{obj.star_rating}-star" + (f" · {room}" if room else ""),
                "description": obj.description,
                "amenities": obj.amenities or [],
                "offer": offer_summary(offer),
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


# What a customer's car-type wish matches in `vehicle_type` or the car's name.
CAR_TYPE_TERMS = {
    "sedan": ("sedan",),
    "suv": ("suv", "muv"),
    "hatchback": ("hatchback",),
    "tempo traveller": ("tempo", "traveller", "van", "minibus"),
}


def _squash(text):
    """`Wi-Fi` and `wifi` compare equal."""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def amenity_matches(item_amenities, wanted):
    have = [_squash(amenity) for amenity in item_amenities or []]
    return sum(1 for want in wanted if any(_squash(want) in amenity for amenity in have))


def _apply_preferences(queryset, kind, *, min_star_rating, car_type, transmission, min_seats):
    """Hard filters the data can actually answer. Amenities only re-rank (see below)."""
    if kind == InventoryType.HOTEL and min_star_rating:
        queryset = queryset.filter(star_rating__gte=min_star_rating)
    if kind == InventoryType.CAR:
        if min_seats:
            queryset = queryset.filter(seats__gte=min_seats)
        terms = CAR_TYPE_TERMS.get(car_type, (car_type,) if car_type else ())
        if terms:
            condition = Q()
            for term in terms:
                condition |= Q(vehicle_type__icontains=term) | Q(name__icontains=term)
            queryset = queryset.filter(condition)
        if transmission:
            queryset = queryset.filter(transmission__icontains=transmission)
    return queryset


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
    min_star_rating=None,
    amenities=(),
    car_type="",
    transmission="",
    min_seats=None,
    check_in=None,
    check_out=None,
):
    """Unified search across hotels, cars and packages.

    Returns normalised dicts sorted by website priority, then by how many of the
    wanted amenities they have, then by price. Star rating, seats, car type and
    transmission are hard filters: a 4-seater is never offered to seven people.
    Hotels are priced (and price-filtered) for `check_in` / `check_out`, or
    tonight when no dates are given. Archived items are never returned.
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
        filters = {"q": q, "status": status, "min_price": min_price, "max_price": max_price}
        if kind == InventoryType.HOTEL:
            filters.update(check_in=check_in, check_out=check_out)
        queryset = listers[kind](**filters)
        if destination:
            queryset = queryset.filter(
                Q(destination__name__icontains=destination)
                | Q(destination__city__icontains=destination)
                | Q(destination__code__icontains=destination)
                | Q(name__icontains=destination)
            )
        queryset = _apply_preferences(
            queryset,
            kind,
            min_star_rating=min_star_rating,
            car_type=car_type,
            transmission=transmission,
            min_seats=min_seats,
        )
        hidden = hidden_ids_for_website(website, kind)
        priorities = priority_map_for_website(website, kind)
        for obj in queryset[: limit * 2]:
            if obj.pk in hidden:
                continue
            item = serialize_item(obj, kind, check_in=check_in, check_out=check_out)
            item["priority"] = priorities.get(obj.pk, 0)
            item["amenity_matches"] = amenity_matches(item["amenities"], amenities)
            results.append(item)

    results.sort(
        key=lambda item: (-item["priority"], -item["amenity_matches"], item["price"] or 0)
    )
    return results[:limit]


def is_hidden_on(website, inventory_type, object_id):
    return object_id in hidden_ids_for_website(website, inventory_type)


def get_inventory_object(inventory_type, object_id):
    model = MODEL_BY_TYPE.get(inventory_type)
    if not model:
        return None
    return model.objects.filter(pk=object_id, is_deleted=False).first()
