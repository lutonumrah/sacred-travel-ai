"""The AI layer behind the customer chat widget.

Two engines, always in this order:

1. **Rules** (always runs). Pulls structured requirements out of the customer's
   message — destination, dates, party size, budget, product type, contact
   details — and matches them against live inventory for that website. This is
   what creates leads and recommendations, so the system keeps working with no
   API key and no network.
2. **Model** (optional). When an API key is configured — under AI Settings in
   the dashboard, or ANTHROPIC_API_KEY / GEMINI_API_KEY in the environment — the
   extracted requirements plus the matched inventory are handed to Claude or
   Gemini, which writes the customer-facing reply, refines the requirements and
   decides whether a human should take over. The model only ever picks from
   inventory the rules layer already retrieved, so it cannot invent hotels or
   prices.

Any failure in step 2 falls back to the templated reply from step 1.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime

from django.conf import settings

from inventory.selectors import search_inventory

from .models import DEFAULT_MODEL, AIProvider, AISettings

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,12}\d")
TRAVELERS_RE = re.compile(
    r"(\d{1,2})\s*(?:people|persons?|pax|adults?|travell?ers?|guests?|of us)", re.I
)
NIGHTS_RE = re.compile(r"(\d{1,2})\s*(?:nights?|days?)", re.I)

# Words that make a customer ask for a person, not a bot.
HANDOFF_PHRASES = (
    "human",
    "agent",
    "real person",
    "talk to someone",
    "speak to someone",
    "customer care",
    "call me",
    "phone me",
    "manager",
    "complaint",
    "refund",
    "cancel my booking",
)

PRODUCT_KEYWORDS = {
    "hotel": ("hotel", "stay", "room", "resort", "accommodation", "lodge"),
    "car": ("car", "cab", "taxi", "vehicle", "self drive", "self-drive", "rental"),
    "package": ("package", "tour", "itinerary", "trip", "holiday", "honeymoon"),
}

GREETING_WORDS = ("hi", "hello", "hey", "namaste", "good morning", "good evening")


@dataclass
class AIResult:
    """What the engine decided for one customer message."""

    reply: str
    requirements: dict = field(default_factory=dict)
    recommendations: list = field(default_factory=list)
    should_handoff: bool = False
    handoff_reason: str = ""
    engine: str = "rules"
    contact: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Rule layer
# --------------------------------------------------------------------------


def _known_destinations():
    from inventory.models import Destination

    return list(
        Destination.objects.filter(is_active=True).values_list("name", "city", "code")
    )


def extract_contact(text):
    """Pull an email address and/or phone number out of free text."""
    contact = {}
    email = EMAIL_RE.search(text)
    if email:
        contact["email"] = email.group(0)

    # Strip the email before hunting for phone numbers so digits inside an
    # address are not mistaken for one.
    stripped = EMAIL_RE.sub(" ", text)
    phone = PHONE_RE.search(stripped)
    if phone:
        digits = re.sub(r"\D", "", phone.group(0))
        if 10 <= len(digits) <= 13:
            contact["phone"] = digits
    return contact


NUMERIC_DATE_PATTERNS = (
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), ("%Y-%m-%d",)),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), ("%d/%m/%Y", "%d-%m-%Y")),
)

MONTH_DATE_PATTERN = re.compile(
    r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+(\d{4}))?",
    re.I,
)

MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def strip_dates(text):
    """Blank out date substrings.

    Budget parsing runs over the result: without this, `12/03/2027` reads as a
    ₹2,027 budget, which then filters away every real option.
    """
    for pattern, _formats in NUMERIC_DATE_PATTERNS:
        text = pattern.sub(" ", text)
    return MONTH_DATE_PATTERN.sub(" ", text)


def _parse_dates(text):
    """Recognise the handful of date formats customers actually type."""
    found = []
    for pattern, formats in NUMERIC_DATE_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0)
            for fmt in formats:
                try:
                    found.append(datetime.strptime(raw, fmt).date())
                    break
                except ValueError:
                    continue

    months = MONTH_NUMBERS
    today = date.today()
    for match in MONTH_DATE_PATTERN.finditer(text):
        day = int(match.group(1))
        month = months[match.group(2).lower()[:3]]
        year = int(match.group(3)) if match.group(3) else today.year
        try:
            parsed = date(year, month, day)
        except ValueError:
            continue
        # A bare day+month in the past almost always means next year.
        if not match.group(3) and parsed < today:
            try:
                parsed = date(year + 1, month, day)
            except ValueError:
                continue
        found.append(parsed)

    found.sort()
    return found


def _parse_budget(text):
    """Understand `40k`, `1.5 lakh`, `₹25,000` and plain numbers."""
    lowered = strip_dates(text).lower().replace(",", "")
    amounts = []
    for match in re.finditer(
        r"(₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(k|thousand|lakh|lac|l\b|cr)?", lowered
    ):
        currency, raw, unit = match.group(1), match.group(2), (match.group(3) or "").strip()
        try:
            value = float(raw)
        except ValueError:
            continue
        if unit in ("k", "thousand"):
            value *= 1_000
        elif unit in ("lakh", "lac", "l"):
            value *= 100_000
        elif unit == "cr":
            value *= 10_000_000
        # A bare four-digit year is a date the caller has not masked, not money.
        elif not currency and 1900 <= value <= 2100 and raw.isdigit():
            continue
        # Below ₹1,000 with no unit is a party size, not a budget.
        if value >= 1000:
            amounts.append(int(value))
    if not amounts:
        return None, None
    if len(amounts) == 1:
        return None, amounts[0]
    return min(amounts), max(amounts)


def _detect_product_type(text):
    lowered = text.lower()
    for kind, words in PRODUCT_KEYWORDS.items():
        if any(word in lowered for word in words):
            return kind
    return ""


def _detect_destination(text):
    lowered = text.lower()
    best = ""
    for name, city, code in _known_destinations():
        for candidate in (name, city, code):
            if candidate and len(candidate) > 2 and candidate.lower() in lowered:
                # Prefer the longest match: "New Delhi" over "Delhi".
                if len(candidate) > len(best):
                    best = candidate
    return best


def extract_requirements(text, previous=None):
    """Merge what this message reveals into everything learned so far."""
    requirements = dict(previous or {})

    destination = _detect_destination(text)
    if destination:
        requirements["destination"] = destination

    product_type = _detect_product_type(text)
    if product_type:
        requirements["product_type"] = product_type

    travelers = TRAVELERS_RE.search(text)
    if travelers:
        requirements["travelers"] = int(travelers.group(1))

    dates = _parse_dates(text)
    if dates:
        requirements["travel_start"] = dates[0].isoformat()
        if len(dates) > 1:
            requirements["travel_end"] = dates[-1].isoformat()

    nights = NIGHTS_RE.search(text)
    if nights and "nights" not in requirements:
        requirements["nights"] = int(nights.group(1))

    budget_min, budget_max = _parse_budget(text)
    if budget_max:
        requirements["budget_max"] = budget_max
    if budget_min:
        requirements["budget_min"] = budget_min

    return requirements


def detect_handoff(text):
    lowered = text.lower()
    for phrase in HANDOFF_PHRASES:
        if phrase in lowered:
            return True, f"Customer asked for a human ('{phrase}')"
    return False, ""


def match_inventory(requirements, website=None, limit=4):
    """Find inventory that fits the requirements gathered so far.

    Returns nothing until the customer has told us something to match on —
    otherwise a plain "hi" comes back with arbitrary cars and hotels attached.
    """
    if not any(
        requirements.get(key) for key in ("destination", "product_type", "budget_max")
    ):
        return []
    return search_inventory(
        q="",
        inventory_type=requirements.get("product_type", ""),
        destination=requirements.get("destination", ""),
        max_price=requirements.get("budget_max"),
        website=website,
        limit=limit,
    )


def _money(item):
    price = item.get("price")
    if price in (None, ""):
        return "price on request"
    return f"{item.get('currency', 'INR')} {int(price):,} {item.get('price_label', '')}".strip()


def compose_rule_reply(message, requirements, recommendations, brand):
    """Deterministic reply used when no AI model is available."""
    lowered = message.strip().lower()

    if any(lowered.startswith(word) for word in GREETING_WORDS) and len(lowered) < 30:
        return (
            f"Hello, and welcome to {brand}. Tell me where you would like to travel, "
            "roughly when, and how many people are going — I will pull up the best options we have."
        )

    if recommendations:
        lines = [
            f"Here {'is' if len(recommendations) == 1 else 'are'} "
            f"{len(recommendations)} option{'s' if len(recommendations) > 1 else ''} "
            f"for {requirements.get('destination') or 'your trip'}:"
        ]
        for item in recommendations:
            detail = item.get("detail", "")
            lines.append(f"• {item['name']} — {detail} — {_money(item)}")
        missing = _missing_fields(requirements)
        if missing:
            lines.append(f"To narrow this down, could you tell me {missing}?")
        else:
            lines.append(
                "Let me know which one you like and I will reserve it for you."
            )
        return "\n".join(lines)

    missing = _missing_fields(requirements)
    if missing:
        return (
            "I can help with that. Could you tell me "
            f"{missing}? Then I will find the right options for you."
        )

    return (
        "I could not find a match in our current inventory for that. "
        "Let me put you through to one of our travel consultants, who can look at more options."
    )


def _missing_fields(requirements):
    missing = []
    if not requirements.get("destination"):
        missing.append("your destination")
    if not requirements.get("travel_start"):
        missing.append("your travel dates")
    if not requirements.get("travelers"):
        missing.append("how many people are travelling")
    if not requirements.get("budget_max"):
        missing.append("your approximate budget")
    if not missing:
        return ""
    if len(missing) == 1:
        return missing[0]
    return ", ".join(missing[:-1]) + f" and {missing[-1]}"


# --------------------------------------------------------------------------
# Model layer (Claude or Gemini)
# --------------------------------------------------------------------------

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {
            "type": "string",
            "description": "The message to send to the customer. Warm, concise, at most 90 words.",
        },
        "requirements": {
            "type": "object",
            "description": "Everything known about the trip so far.",
            "properties": {
                "destination": {"type": "string"},
                "product_type": {"type": "string", "enum": ["hotel", "car", "package", ""]},
                "travel_start": {"type": "string"},
                "travel_end": {"type": "string"},
                "travelers": {"type": "integer"},
                "budget_max": {"type": "integer"},
            },
            "required": [
                "destination",
                "product_type",
                "travel_start",
                "travel_end",
                "travelers",
                "budget_max",
            ],
            "additionalProperties": False,
        },
        "recommended_ids": {
            "type": "array",
            "description": "IDs from the supplied inventory list, best first. Never invent one.",
            "items": {"type": "string"},
        },
        "should_handoff": {
            "type": "boolean",
            "description": "True when a human agent should take over this chat.",
        },
        "handoff_reason": {"type": "string"},
    },
    "required": [
        "reply",
        "requirements",
        "recommended_ids",
        "should_handoff",
        "handoff_reason",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the travel assistant for {brand}, an Indian travel agency.

Your job is to help the customer plan their trip and to steer them towards booking \
something the agency actually sells.

Rules you must follow:
- Recommend ONLY from the inventory list given below. Never invent a hotel, car, \
package, price or availability. If nothing fits, say so and offer a human consultant.
- Quote prices exactly as they appear in the inventory list, in the currency shown.
- Ask at most one or two questions per reply. Prioritise: destination, travel dates, \
number of travellers, budget.
- Keep replies under 90 words and write plain conversational prose, not markdown.
- Set should_handoff to true if the customer asks for a human, is upset, wants a refund \
or a cancellation, or asks something you cannot answer from the inventory.
- In `requirements`, carry forward everything already known and add anything new. Use \
empty string or 0 for anything still unknown. Dates are YYYY-MM-DD.
- In `recommended_ids`, return the ids of inventory items you referenced, best first.

Known so far about this trip: {known}

Inventory available for this website:
{inventory}"""


@dataclass(frozen=True)
class AIConfig:
    """The provider, model and key one chat turn will use."""

    provider: str
    model: str
    api_key: str


def resolve_config(stored=None, *, respect_enabled=True):
    """Work out which AI to call, or None when replies stay rule-based.

    Saved AI Settings win; the environment fills in anything left blank.
    """
    if respect_enabled and not getattr(settings, "AI_ENABLED", True):
        return None
    stored = stored or AISettings.load()
    if respect_enabled and not stored.enabled:
        return None

    provider = stored.provider
    env_key = {
        AIProvider.ANTHROPIC: getattr(settings, "ANTHROPIC_API_KEY", ""),
        AIProvider.GEMINI: getattr(settings, "GEMINI_API_KEY", ""),
    }.get(provider, "")
    api_key = stored.stored_key(provider) or env_key
    if not api_key:
        return None
    # Never saved by an admin: the environment's AI_MODEL decides.
    model = getattr(settings, "AI_MODEL", "") if stored._state.adding else stored.model
    return AIConfig(provider=provider, model=model or DEFAULT_MODEL[provider], api_key=api_key)


def _inventory_block(items):
    if not items:
        return "(no inventory matches the requirements gathered so far)"
    lines = []
    for item in items:
        item_id = f"{item['inventory_type']}:{item['id']}"
        lines.append(
            f"- id={item_id} | {item['name']} | {item.get('destination') or 'n/a'} "
            f"| {item.get('detail', '')} | {_money(item)}"
        )
    return "\n".join(lines)


def _system_prompt(*, brand, requirements, inventory):
    return SYSTEM_PROMPT.format(
        brand=brand,
        known=json.dumps(requirements, default=str) if requirements else "nothing yet",
        inventory=_inventory_block(inventory),
    )


def _handoff_answer(requirements, reason):
    return {
        "reply": "Let me bring in one of our travel consultants to help you with this.",
        "requirements": requirements,
        "recommended_ids": [],
        "should_handoff": True,
        "handoff_reason": reason,
    }


# --- Claude ---------------------------------------------------------------

# Older models that reject adaptive thinking and `effort`.
CLAUDE_NO_THINKING_PREFIXES = (
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4-0",
    "claude-sonnet-4-0",
    "claude-3",
)
# Models that accept the server-side refusal fallback: if a safety classifier
# declines a chat turn, the API retries it on another model in the same call.
CLAUDE_FALLBACK_MODELS = {
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}


def _client(config=None):
    if config is None:
        config = resolve_config()
    if config is None or config.provider != AIProvider.ANTHROPIC:
        return None
    try:
        import anthropic
    except ImportError:
        logger.info("anthropic SDK not installed — using the rule-based chat engine.")
        return None
    return anthropic.Anthropic(
        api_key=config.api_key, timeout=getattr(settings, "AI_TIMEOUT_SECONDS", 30)
    )


def _ask_claude(config, *, system, requirements, history, message):
    client = _client(config)
    if client is None:
        return None

    model = config.model if config else getattr(settings, "AI_MODEL", DEFAULT_MODEL["anthropic"])
    output_config = {"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}}
    params = {
        "model": model,
        "max_tokens": 8000,
        "system": system,
        "messages": list(history) + [{"role": "user", "content": message}],
    }
    if not model.startswith(CLAUDE_NO_THINKING_PREFIXES):
        params["thinking"] = {"type": "adaptive"}
        output_config["effort"] = "low"
    params["output_config"] = output_config

    try:
        if model in CLAUDE_FALLBACK_MODELS:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
            )
        else:
            response = client.messages.create(**params)
    except Exception:
        logger.exception("Claude call failed — falling back to the rule-based reply.")
        return None

    if getattr(response, "stop_reason", None) == "refusal":
        logger.warning("Claude declined this chat turn; handing off to a human.")
        return _handoff_answer(requirements, "AI declined to answer")

    try:
        text = next(block.text for block in response.content if block.type == "text")
        return json.loads(text)
    except (StopIteration, json.JSONDecodeError, AttributeError):
        logger.exception("Could not read structured output from Claude.")
        return None


# --- Gemini ---------------------------------------------------------------

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_BLOCKED_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


def _gemini_model_path(model):
    model = model.removeprefix("models/")
    return f"{GEMINI_API_BASE}/models/{urllib.parse.quote(model, safe='.-_')}"


def _gemini_request(url, api_key, body=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    timeout = getattr(settings, "AI_TIMEOUT_SECONDS", 30)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _ask_gemini(config, *, system, requirements, history, message):
    contents = [
        {
            "role": "model" if turn["role"] == "assistant" else "user",
            "parts": [{"text": str(turn["content"])}],
        }
        for turn in history
    ]
    contents.append({"role": "user", "parts": [{"text": message}]})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": RESPONSE_SCHEMA,
        },
    }

    try:
        data = _gemini_request(f"{_gemini_model_path(config.model)}:generateContent", config.api_key, body)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode(errors="replace")
        logger.error("Gemini call failed (%s): %s — falling back to rules.", exc.code, detail)
        return None
    except (urllib.error.URLError, TimeoutError, ValueError):
        logger.exception("Gemini call failed — falling back to the rule-based reply.")
        return None

    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer")
        logger.warning("Gemini returned no candidates (%s); handing off to a human.", reason)
        return _handoff_answer(requirements, "AI declined to answer")
    candidate = candidates[0]
    if candidate.get("finishReason") in GEMINI_BLOCKED_REASONS:
        logger.warning("Gemini blocked this chat turn (%s).", candidate.get("finishReason"))
        return _handoff_answer(requirements, "AI declined to answer")

    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.exception("Could not read structured output from Gemini.")
        return None


def _ask_model(*, brand, requirements, inventory, history, message):
    """Ask the configured AI for this turn. Returns (answer, engine) or (None, "")."""
    config = resolve_config()
    system = _system_prompt(brand=brand, requirements=requirements, inventory=inventory)
    turn = {"system": system, "requirements": requirements, "history": history, "message": message}
    if config is not None and config.provider == AIProvider.GEMINI:
        return _ask_gemini(config, **turn), "gemini"
    return _ask_claude(config, **turn), "claude"


def check_connection(config):
    """Confirm a key works and the model exists, without generating anything.

    Returns (ok, message) for the settings page.
    """
    if config.provider == AIProvider.GEMINI:
        try:
            info = _gemini_request(_gemini_model_path(config.model), config.api_key)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False, f"Gemini has no model called {config.model}."
            if exc.code in (400, 401, 403):
                return False, "Google rejected the Gemini API key."
            return False, f"Gemini returned HTTP {exc.code}."
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return False, f"Could not reach Gemini: {exc}"
        return True, f"Connected — {info.get('displayName') or config.model} is available."

    try:
        import anthropic
    except ImportError:
        return False, "The anthropic package is not installed on the server."
    client = _client(config)
    try:
        info = client.models.retrieve(config.model)
    except anthropic.AuthenticationError:
        return False, "Anthropic rejected the API key."
    except anthropic.PermissionDeniedError:
        return False, "This API key is not allowed to use that model."
    except anthropic.NotFoundError:
        return False, f"Anthropic has no model called {config.model}."
    except anthropic.APIConnectionError:
        return False, "Could not reach Anthropic."
    except anthropic.APIStatusError as exc:
        return False, f"Anthropic returned HTTP {exc.status_code}."
    return True, f"Connected — {info.display_name or config.model} is available."


def _select_by_ids(inventory, ids):
    """Resolve the ids the model returned back to real inventory dicts."""
    by_key = {f"{item['inventory_type']}:{item['id']}": item for item in inventory}
    picked = [by_key[key] for key in ids if key in by_key]
    return picked or inventory[:3]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def generate_reply(*, conversation, message, history=None):
    """Produce the assistant's answer to one customer message."""
    website = conversation.website
    brand = "Scared Travel"
    if website:
        brand = website.brand_name or website.name

    requirements = extract_requirements(message, conversation.requirements or {})
    contact = extract_contact(message)
    inventory = match_inventory(requirements, website=website, limit=6)
    handoff, handoff_reason = detect_handoff(message)

    answer, engine = _ask_model(
        brand=brand,
        requirements=requirements,
        inventory=inventory,
        history=history or [],
        message=message,
    )

    if answer:
        merged = dict(requirements)
        for key, value in (answer.get("requirements") or {}).items():
            if value not in (None, "", 0):
                merged[key] = value
        recommendations = _select_by_ids(inventory, answer.get("recommended_ids") or [])
        model_handoff = bool(answer.get("should_handoff"))
        return AIResult(
            reply=answer.get("reply") or compose_rule_reply(
                message, merged, recommendations, brand
            ),
            requirements=merged,
            recommendations=recommendations,
            should_handoff=handoff or model_handoff,
            handoff_reason=handoff_reason or answer.get("handoff_reason", ""),
            engine=engine,
            contact=contact,
        )

    recommendations = inventory[:3]
    return AIResult(
        reply=compose_rule_reply(message, requirements, recommendations, brand),
        requirements=requirements,
        recommendations=recommendations,
        should_handoff=handoff,
        handoff_reason=handoff_reason,
        engine="rules",
        contact=contact,
    )
