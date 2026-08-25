from django.db.models import Q

from .models import Team, User


def list_users(*, q="", role="", status=""):
    queryset = User.objects.all()
    if q:
        queryset = queryset.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
        )
    if role:
        queryset = queryset.filter(role=role)
    if status == "active":
        queryset = queryset.filter(is_active=True, is_active_employee=True)
    elif status == "inactive":
        queryset = queryset.filter(Q(is_active=False) | Q(is_active_employee=False))
    return queryset


def list_teams():
    return Team.objects.prefetch_related("members").all()


def assignable_users():
    """Users who can own a lead or a conversation."""
    return User.objects.filter(is_active=True, is_active_employee=True).exclude(
        role="inventory"
    )
