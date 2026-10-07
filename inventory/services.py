from django.utils import timezone

from core.services import log_audit

from .models import InventoryWebsiteVisibility


def record_inventory_saved(*, obj, actor=None, request=None, created=False, kind="item"):
    log_audit(
        actor=actor,
        action=f"inventory.{kind}.{'create' if created else 'update'}",
        entity=obj,
        metadata={"name": obj.name, "active": obj.is_active},
        request=request,
    )
    return obj


class InventoryError(Exception):
    """An archive / restore / delete that is refused; the message is shown to staff."""


def archive(*, obj, actor=None, request=None, kind="item"):
    """Soft delete: inactive and out of lists, search, the AI and new bookings."""
    from .selectors import items_using_destination

    if kind == "destination":
        in_use = sum(
            queryset.count()
            for queryset in items_using_destination(obj, include_archived=False).values()
        )
        if in_use:
            raise InventoryError(
                f"{obj.name} is used by {in_use} hotel, car or package"
                f"{'s' if in_use > 1 else ''}. Archive or move them first."
            )
    obj.is_deleted = True
    obj.deleted_at = timezone.now()
    obj.is_active = False
    obj.save(update_fields=["is_deleted", "deleted_at", "is_active", "updated_at"])
    log_audit(
        actor=actor,
        action=f"inventory.{kind}.archive",
        entity=obj,
        metadata={"name": obj.name},
        request=request,
    )
    return obj


# Kept for callers that predate archive/restore.
soft_delete = archive


def restore(*, obj, actor=None, request=None, kind="item"):
    """Bring an archived item back, still inactive until someone enables it."""
    obj.is_deleted = False
    obj.deleted_at = None
    obj.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    log_audit(
        actor=actor,
        action=f"inventory.{kind}.restore",
        entity=obj,
        metadata={"name": obj.name},
        request=request,
    )
    return obj


def delete_permanently(*, obj, actor=None, request=None, kind="item"):
    """Hard delete an archived item nothing refers to.

    Bookings point at inventory by type + id, so deleting a booked item would
    orphan its history: that is refused, and the item can only stay archived.
    """
    from .selectors import bookings_for, items_using_destination

    if not obj.is_deleted:
        raise InventoryError(f"Archive {obj.name} before deleting it permanently.")
    booked = bookings_for(kind, obj).count()
    if booked:
        raise InventoryError(
            f"{obj.name} has {booked} booking{'s' if booked > 1 else ''} and cannot be "
            "deleted. It stays archived, hidden from search and the AI."
        )
    if kind == "destination":
        used = sum(queryset.count() for queryset in items_using_destination(obj).values())
        if used:
            raise InventoryError(
                f"{obj.name} is still set on {used} hotel, car or package"
                f"{'s' if used > 1 else ''} (archived ones included) and cannot be deleted."
            )
    name, pk = obj.name, obj.pk
    log_audit(
        actor=actor,
        action=f"inventory.{kind}.delete",
        entity=obj,
        metadata={"name": name},
        request=request,
    )
    if kind != "destination":
        InventoryWebsiteVisibility.objects.filter(inventory_type=kind, object_id=pk).delete()
    obj.delete()
    return name


def toggle_active(*, obj, actor=None, request=None, kind="item"):
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active", "updated_at"])
    log_audit(
        actor=actor,
        action=f"inventory.{kind}.toggle_active",
        entity=obj,
        metadata={"active": obj.is_active},
        request=request,
    )
    return obj


def set_visibility(*, website, inventory_type, object_id, is_visible, priority=0, actor=None, request=None):
    row, _created = InventoryWebsiteVisibility.objects.update_or_create(
        website=website,
        inventory_type=inventory_type,
        object_id=object_id,
        defaults={"is_visible": is_visible, "priority": priority},
    )
    log_audit(
        actor=actor,
        action="inventory.visibility.set",
        entity=website,
        metadata={
            "inventory_type": inventory_type,
            "object_id": object_id,
            "is_visible": is_visible,
            "priority": priority,
        },
        request=request,
    )
    return row
