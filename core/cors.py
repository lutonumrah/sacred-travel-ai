"""Cross-origin access for the embeddable widget, website enquiry form and payment APIs.

django-cors-headers only ever answers for paths matching CORS_URLS_REGEX;
the staff API never gets CORS headers. Inside that, an Origin is allowed when
it belongs to a registered website:

* Preflight (OPTIONS) carries no body, so no widget key: the Origin must match
  ANY active website with the widget switched on.
* The real request: the view records which website the key (or payment token)
  belongs to on `request.cors_website`, and the Origin must match THAT site.
  The enquiry-form snippet posts FormData, a "simple" request with no
  preflight, so it works even on a site whose chat widget is switched off.
  If the view never got that far (bad key, throttled) we fall back to the
  preflight rule so the browser can still read the error.

Origin matching for a website whose domain is `example.com`:

* host `example.com`, `www.example.com` or any `*.example.com` subdomain;
* scheme http or https;
* port: none (i.e. the scheme default) unless the domain itself names one,
  e.g. `localhost:3000`, which must then match exactly.

Nothing else — `example.com.evil.net` or `notexample.com` never match.
"""

import re
from urllib.parse import urlsplit

from django.conf import settings


def _split_domain(domain):
    """`https://www.Example.com:8443/x` → (`example.com`, 8443)."""
    domain = (domain or "").strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain).split("/")[0]
    host, _, port = domain.partition(":")
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host, int(port) if port.isdigit() else None


def origin_matches_domain(origin, domain):
    try:
        url = urlsplit(origin)
        origin_port = url.port
    except ValueError:
        return False
    if url.scheme not in ("http", "https") or not url.hostname:
        return False
    host, port = _split_domain(domain)
    if not host:
        return False
    if port != origin_port:
        return False
    origin_host = url.hostname.lower().rstrip(".")
    return origin_host == host or origin_host.endswith("." + host)


def origin_allowed_for_any_website(origin):
    from websites.models import Website

    domains = Website.objects.filter(
        is_active=True, is_deleted=False, widget_enabled=True
    ).values_list("domain", flat=True)
    return any(origin_matches_domain(origin, domain) for domain in domains)


def allow_cors_for(request, website):
    """Called by a public view once it knows which website the caller is acting for."""
    # DRF wraps the Django request; the middleware only sees the inner one.
    target = getattr(request, "_request", request)
    target.cors_website = website


def check_request_enabled(sender, request, **kwargs):
    """`corsheaders.signals.check_request_enabled` receiver."""
    # The middleware also asks this signal for paths outside CORS_URLS_REGEX;
    # answering True there would open the staff API.
    if not re.match(settings.CORS_URLS_REGEX, request.path_info):
        return False
    origin = request.headers.get("origin")
    if not origin:
        return False
    website = getattr(request, "cors_website", None)
    if website is not None:
        return origin_matches_domain(origin, website.domain)
    return origin_allowed_for_any_website(origin)
