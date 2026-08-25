"""Shared services used across every module."""

from accounts.models import AuditLog


def get_client_ip(request):
    """Best-effort client IP, honouring a single proxy hop."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def log_audit(*, actor=None, action, entity=None, entity_id="", metadata=None, request=None):
    """Write an audit trail row for a sensitive action.

    `entity` may be a model instance (its class name and pk are recorded) or a
    plain string label.
    """
    if entity is None:
        entity_type = ""
    elif isinstance(entity, str):
        entity_type = entity
    else:
        entity_type = entity.__class__.__name__
        entity_id = entity_id or str(entity.pk)

    return AuditLog.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        metadata=metadata or {},
        ip_address=get_client_ip(request) if request else None,
    )
