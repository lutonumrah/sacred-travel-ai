from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.notifications import notify, notify_managers
from core.services import log_audit

from .models import Customer, Lead, LeadActivity, LeadNote, LeadStatus

# Statuses that mean the lead is no longer being actively worked.
CLOSED_STATUSES = {LeadStatus.CONVERTED, LeadStatus.LOST}

# How far along the pipeline each open status is, for automatic moves that must
# only ever go forward. Follow-up sits beside Qualified: a person put it there.
PIPELINE_RANK = {
    LeadStatus.NEW: 0,
    LeadStatus.QUALIFIED: 1,
    LeadStatus.FOLLOW_UP: 1,
    LeadStatus.INTERESTED: 2,
    LeadStatus.PAYMENT_PENDING: 3,
}


def score_lead(lead):
    """Heuristic 0–100 score used to sort the pipeline and flag hot leads.

    Points come from how much the lead has told us and how far it has moved:
    contact details, travel dates, budget and pipeline position.
    """
    score = 0
    customer = lead.customer
    if customer:
        if customer.phone or customer.whatsapp:
            score += 15
        if customer.email:
            score += 10
    if lead.destination:
        score += 10
    if lead.travel_start:
        score += 15
    if lead.budget_max or lead.budget_min:
        score += 15
    if lead.travelers_count and lead.travelers_count > 1:
        score += 5

    stage_points = {
        LeadStatus.NEW: 0,
        LeadStatus.QUALIFIED: 10,
        LeadStatus.FOLLOW_UP: 10,
        LeadStatus.INTERESTED: 20,
        LeadStatus.PAYMENT_PENDING: 30,
        LeadStatus.CONVERTED: 30,
        LeadStatus.LOST: 0,
    }
    score += stage_points.get(lead.status, 0)
    return max(0, min(100, score))


def record_activity(*, lead, actor=None, activity_type, summary, details=None):
    return LeadActivity.objects.create(
        lead=lead,
        actor=actor if (actor and actor.is_authenticated) else None,
        activity_type=activity_type,
        summary=summary,
        details=details or {},
    )


def refresh_score(lead, *, save=True):
    lead.score = score_lead(lead)
    if save:
        Lead.objects.filter(pk=lead.pk).update(score=lead.score)
    return lead.score


@transaction.atomic
def create_lead(*, lead, actor=None, request=None, activity_summary=None):
    """Persist a new lead, score it and tell the assignee."""
    lead.score = score_lead(lead)
    lead.save()
    record_activity(
        lead=lead,
        actor=actor,
        activity_type="created",
        summary=activity_summary or f"Lead created from {lead.get_source_display()}",
        details={"status": lead.status, "score": lead.score},
    )
    log_audit(
        actor=actor,
        action="lead.create",
        entity=lead,
        metadata={"status": lead.status, "source": lead.source},
        request=request,
    )
    link = reverse("crm:lead_detail", args=[lead.pk])
    if lead.assigned_to:
        notify(
            recipient=lead.assigned_to,
            notification_type="lead",
            title=f"New lead assigned: {lead.title}",
            body=lead.destination or "",
            link=link,
            metadata={"lead_id": lead.pk},
        )
    else:
        notify_managers(
            notification_type="lead",
            title=f"New lead: {lead.title}",
            body=f"Source: {lead.get_source_display()}",
            link=link,
            metadata={"lead_id": lead.pk},
            exclude=actor if (actor and actor.is_authenticated) else None,
        )
    return lead


@transaction.atomic
def update_lead(*, lead, actor=None, request=None, changed_fields=None):
    lead.score = score_lead(lead)
    lead.save()
    record_activity(
        lead=lead,
        actor=actor,
        activity_type="updated",
        summary="Lead details updated",
        details={"changed": list(changed_fields or [])},
    )
    log_audit(actor=actor, action="lead.update", entity=lead, request=request)
    return lead


@transaction.atomic
def change_status(*, lead, status, actor=None, request=None, lost_reason="", note=""):
    """Move a lead through the pipeline, recording history and notifying."""
    previous = lead.status
    if previous == status:
        return lead

    lead.status = status
    lead.lost_reason = lost_reason if status == LeadStatus.LOST else ""
    if status == LeadStatus.CONVERTED and lead.converted_at is None:
        lead.converted_at = timezone.now()
    lead.score = score_lead(lead)
    lead.save(
        update_fields=["status", "lost_reason", "converted_at", "score", "updated_at"]
    )

    record_activity(
        lead=lead,
        actor=actor,
        activity_type="status_change",
        summary=f"{LeadStatus(previous).label} → {LeadStatus(status).label}",
        details={"from": previous, "to": status, "lost_reason": lost_reason, "note": note},
    )
    log_audit(
        actor=actor,
        action="lead.status_change",
        entity=lead,
        metadata={"from": previous, "to": status},
        request=request,
    )

    link = reverse("crm:lead_detail", args=[lead.pk])
    if status in CLOSED_STATUSES:
        notify_managers(
            notification_type="lead",
            title=f"Lead {LeadStatus(status).label.lower()}: {lead.title}",
            body=lost_reason or (lead.destination or ""),
            link=link,
            metadata={"lead_id": lead.pk},
            exclude=actor if (actor and actor.is_authenticated) else None,
        )
    elif lead.assigned_to and lead.assigned_to != actor:
        notify(
            recipient=lead.assigned_to,
            notification_type="lead",
            title=f"{lead.title} moved to {LeadStatus(status).label}",
            link=link,
            metadata={"lead_id": lead.pk},
        )
    return lead


def advance_status(*, lead, status, note="", actor=None, request=None):
    """Automatic pipeline move: forward only, and never out of Converted or Lost.

    Returns True when the lead moved.
    """
    current = PIPELINE_RANK.get(lead.status)
    target = PIPELINE_RANK.get(status)
    if current is None or target is None or target <= current:
        return False
    change_status(lead=lead, status=status, actor=actor, request=request, note=note)
    return True


def is_qualified(lead, requirements):
    """Destination, when, how many, and a way to reach the customer are all known."""
    requirements = requirements or {}
    customer = lead.customer
    has_contact = bool(customer and (customer.email or customer.phone))
    has_when = bool(
        lead.travel_start or requirements.get("travel_start") or requirements.get("travel_month")
    )
    # `travelers_count` defaults to 1, so only an explicit answer counts.
    has_party = bool(requirements.get("travelers"))
    return bool(lead.destination) and has_when and has_party and has_contact


def qualify_if_ready(*, lead, requirements, request=None):
    if lead.status == LeadStatus.NEW and is_qualified(lead, requirements):
        return advance_status(
            lead=lead,
            status=LeadStatus.QUALIFIED,
            note="Automatic: destination, dates, party size and contact captured",
            request=request,
        )
    return False


@transaction.atomic
def assign_lead(*, lead, user=None, team=None, actor=None, request=None):
    lead.assigned_to = user
    lead.assigned_team = team
    lead.save(update_fields=["assigned_to", "assigned_team", "updated_at"])
    target = user.get_username() if user else (team.name if team else "nobody")
    record_activity(
        lead=lead,
        actor=actor,
        activity_type="assignment",
        summary=f"Assigned to {target}",
        details={"user_id": user.pk if user else None, "team_id": team.pk if team else None},
    )
    log_audit(
        actor=actor,
        action="lead.assign",
        entity=lead,
        metadata={"assigned_to": target},
        request=request,
    )
    if user and user != actor:
        notify(
            recipient=user,
            notification_type="lead",
            title=f"Lead assigned to you: {lead.title}",
            body=lead.destination or "",
            link=reverse("crm:lead_detail", args=[lead.pk]),
            metadata={"lead_id": lead.pk},
        )
    return lead


def add_note(*, lead, body, author=None, is_internal=True):
    note = LeadNote.objects.create(
        lead=lead, body=body, author=author, is_internal=is_internal
    )
    record_activity(
        lead=lead,
        actor=author,
        activity_type="note",
        summary=body[:120],
        details={"is_internal": is_internal},
    )
    return note


@transaction.atomic
def create_follow_up(*, task, actor=None, request=None):
    task.save()
    record_activity(
        lead=task.lead,
        actor=actor,
        activity_type="follow_up",
        summary=f"Follow-up scheduled: {task.title}",
        details={"due_at": task.due_at.isoformat()},
    )
    log_audit(actor=actor, action="follow_up.create", entity=task, request=request)
    if task.assigned_to and task.assigned_to != actor:
        notify(
            recipient=task.assigned_to,
            notification_type="follow_up",
            title=f"Follow-up: {task.title}",
            body=f"Due {task.due_at:%d %b %Y %H:%M}",
            link=reverse("crm:lead_detail", args=[task.lead_id]),
            metadata={"task_id": task.pk},
        )
    return task


def complete_follow_up(*, task, actor=None, request=None):
    task.is_completed = True
    task.completed_at = timezone.now()
    task.save(update_fields=["is_completed", "completed_at", "updated_at"])
    record_activity(
        lead=task.lead,
        actor=actor,
        activity_type="follow_up_done",
        summary=f"Follow-up completed: {task.title}",
    )
    log_audit(actor=actor, action="follow_up.complete", entity=task, request=request)
    return task


def get_or_create_customer(*, first_name, last_name="", email="", phone="", **extra):
    """Reuse an existing customer when the email or phone already exists."""
    from .selectors import find_customer

    existing = find_customer(email=email, phone=phone)
    if existing:
        updated = []
        if email and not existing.email:
            existing.email = email
            updated.append("email")
        if phone and not existing.phone:
            existing.phone = phone
            updated.append("phone")
        if updated:
            existing.save(update_fields=updated + ["updated_at"])
        return existing, False

    customer = Customer.objects.create(
        first_name=first_name or "Guest",
        last_name=last_name,
        email=email,
        phone=phone,
        **extra,
    )
    return customer, True
