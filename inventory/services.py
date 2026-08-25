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


def soft_delete(*, obj, actor=None, request=None, kind="item"):
    obj.is_deleted = True
    obj.deleted_at = timezone.now()
    obj.is_active = False
    obj.save(update_fields=["is_deleted", "deleted_at", "is_active", "updated_at"])
    log_audit(
        actor=actor,
        action=f"inventory.{kind}.delete",
        entity=obj,
        metadata={"name": obj.name},
        request=request,
    )
    return obj


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
