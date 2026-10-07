"""One answer to "may this user open that page?" for views, APIs and templates.

Views declare `allowed_roles` (see `core.mixins`); templates ask `can_open`
with the page's URL name, which looks up that same view class. A button is
therefore shown exactly when its view would let the user in, and the two
cannot drift apart.
"""

from functools import lru_cache

from django.urls import URLPattern, URLResolver, get_resolver, get_urlconf


def has_role(user, roles):
    """Signed-in user whose role is in `roles`; superusers and empty `roles` always pass."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser or not roles:
        return True
    return user.role in roles


def _find(resolver, name):
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLPattern) and pattern.name == name:
            return pattern
        # Includes without a namespace share their parent's names.
        if isinstance(pattern, URLResolver) and not pattern.namespace:
            found = _find(pattern, name)
            if found is not None:
                return found
    return None


@lru_cache(maxsize=None)
def _view_class(urlconf, url_name):
    *namespaces, name = url_name.split(":")
    resolver = get_resolver(urlconf)
    for namespace in namespaces:
        resolver = resolver.namespace_dict[namespace][1]
    pattern = _find(resolver, name)
    if pattern is None:
        raise LookupError(f"No URL named {url_name!r}.")
    return getattr(pattern.callback, "view_class", None)


def view_class(url_name):
    return _view_class(get_urlconf(), url_name)


def can_open(user, url_name):
    """Whether `user` passes the role check of the view behind `url_name`."""
    if not (user and user.is_authenticated):
        return False
    cls = view_class(url_name)
    return has_role(user, getattr(cls, "allowed_roles", ()))
