import secrets

from django.utils import timezone

from core.services import log_audit

from .models import WebsiteAPIKey


def _token(nbytes):
    return secrets.token_urlsafe(nbytes)


def issue_api_key(*, website, key_name="Default widget key", actor=None, request=None):
    """Create a widget key pair. The secret is only meaningful going forward."""
    api_key = WebsiteAPIKey.objects.create(
        website=website,
        key_name=key_name,
        public_key=f"pk_{_token(24)}"[:64],
        secret_key=f"sk_{_token(48)}"[:128],
    )
    log_audit(
        actor=actor,
        action="website.api_key.issue",
        entity=website,
        metadata={"key_name": key_name, "public_key": api_key.public_key},
        request=request,
    )
    return api_key


def revoke_api_key(*, api_key, actor=None, request=None):
    api_key.is_active = False
    api_key.save(update_fields=["is_active", "updated_at"])
    log_audit(
        actor=actor,
        action="website.api_key.revoke",
        entity=api_key.website,
        metadata={"public_key": api_key.public_key},
        request=request,
    )
    return api_key


def touch_api_key(api_key):
    """Record widget usage without bumping `updated_at` semantics elsewhere."""
    WebsiteAPIKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())


def record_website_saved(*, website, actor=None, request=None, created=False):
    log_audit(
        actor=actor,
        action="website.create" if created else "website.update",
        entity=website,
        metadata={"domain": website.domain, "source": website.source_identifier},
        request=request,
    )
    if created and not website.api_keys.exists():
        issue_api_key(website=website, actor=actor, request=request)
    return website


def soft_delete_website(*, website, actor=None, request=None):
    website.is_deleted = True
    website.deleted_at = timezone.now()
    website.is_active = False
    website.save(update_fields=["is_deleted", "deleted_at", "is_active", "updated_at"])
    log_audit(actor=actor, action="website.delete", entity=website, request=request)
    return website
