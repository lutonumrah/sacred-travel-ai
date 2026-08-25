from core.services import log_audit


def record_user_saved(*, user, actor=None, request=None, created=False):
    log_audit(
        actor=actor,
        action="user.create" if created else "user.update",
        entity=user,
        metadata={"role": user.role, "username": user.username},
        request=request,
    )
    return user


def record_team_saved(*, team, actor=None, request=None, created=False):
    log_audit(
        actor=actor,
        action="team.create" if created else "team.update",
        entity=team,
        metadata={"members": team.members.count()},
        request=request,
    )
    return team
