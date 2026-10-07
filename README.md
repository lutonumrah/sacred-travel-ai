# Scared Travel AI Operating System

A Django application that runs a travel agency end to end: an AI chat widget on
your customer-facing websites captures leads, a CRM works those leads through a
pipeline, inventory feeds what the AI is allowed to recommend, and Razorpay
takes the payment that closes the booking.

Everything runs offline out of the box. The AI chat works without an API key
(rule-based engine) and payments work without Razorpay keys (local simulation),
so you can develop, demo and test the whole flow with no external accounts.

---

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python manage.py migrate
python manage.py seed_demo          # demo websites, inventory, leads, bookings
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in as `admin` / `travel1234`.
Other demo accounts: `manager`, `agent1`, `agent2`, `stock` (same password).

Prefer an empty system? Skip `seed_demo` and run `python manage.py createsuperuser`.

---

## How it works

### 1. Websites are the entry point

Every brand you operate is a `Website` with a `source_identifier`. Registering
one issues a **public widget key**, and every lead, conversation and booking
that arrives through it is tagged with that source — which is what makes the
per-website analytics meaningful.

Each website's detail page shows a ready-made embed snippet:

```html
<script src="https://your-host/static/js/widget.js"
        data-scared-key="pk_..."
        data-scared-api="https://your-host"
        data-scared-color="#0F766E"
        data-scared-title="Your Brand" defer></script>
```

Try it without deploying anything at **Conversations → Widget preview** — the
bubble there is the real widget hitting the real API.

The widget API answers cross-origin requests only from the website's own
`domain` — the bare domain, `www.` and any subdomain, over http or https (a
domain saved with a port, e.g. `localhost:3000`, must match that port). Each
request is checked against the site its key belongs to; the staff API never
sends CORS headers. On phones the open chat goes full screen, the chat survives
a page reload, and the widget polls while a consultant is involved or a
payment is outstanding.

### 2. The AI answers, and quietly builds a lead

`POST /api/v1/conversations/widget/chat/` is public, authenticated by the
website's public key rather than a session. Each customer message runs through
two layers in [`conversations/ai.py`](conversations/ai.py):

**Layer 1 — rules (always runs).** Extracts structured requirements from the
message and merges them with everything learned in earlier turns:

| Extracted | Understands |
|---|---|
| Destination | matched against your real `Destination` records only |
| Product type | hotel / car / package, from keywords |
| Travel dates | `2027-03-12`, `12/03/2027`, `12 March 2027` |
| Party size | "4 people", "6 adults" |
| Budget | `40k`, `1.5 lakh`, `₹25,000` (a range fills both min and max) |
| Preferences | hotel stars, amenities, food (veg/Jain/halal), car type, transmission, trip style |
| Contact | email and phone anywhere in the text |
| Handoff | "talk to a human", "refund", "cancel my booking" |

It then searches live inventory for matches, honouring per-website visibility.
Preferences the data can answer are applied: a minimum star rating, car type,
transmission and — always — enough seats for the whole party; wanted amenities
rank hotels higher.

**Layer 2 — Claude or Gemini (optional).** An admin picks the provider, model
and API key under **AI Settings** in the dashboard (keys are write-only and never
shown again; *Test connection* checks them for free). The extracted
requirements and the *already-retrieved* inventory go to that model, which
writes the customer-facing reply, refines the requirements and decides whether a
human should step in. The model only ever picks from the inventory the rules
layer retrieved, so **it cannot invent a hotel, a price or availability**. Any
failure — no key, no network, malformed output, a refusal — falls back to the
templated reply. The transcript records which engine answered each turn.

**Lead capture.** As soon as the chat yields either a destination or a contact
detail, a `Lead` is created (source: AI Chat) and linked to the conversation.
Later turns enrich that same lead rather than creating duplicates, and a
matching email or phone reuses the existing `Customer` instead of duplicating
the person. A lead moves itself from **New** to **Qualified** once destination,
dates, party size and a contact are all known, and to **Interested** when the
customer picks an option; automatic moves only ever go forward and never touch
Converted or Lost leads.

**Booking from the chat.** Each recommendation card has a *Book this* button.
The customer confirms dates, travellers and any missing contact details; the
server prices the item itself (hotel: nightly rate × nights; car: daily rate ×
days, pick-up and drop-off day both counted, and the car must seat everyone;
package: per-person price × travellers), adds tax, raises a pending booking and
answers with a no-login payment link (`/pay/<token>/`, valid
`PAYMENT_LINK_TTL_DAYS`, default 7). Agents can do the same from the
conversation page (*Book this for the customer*), and copy or reissue the link
from the booking page — reissuing kills the old link.

### 3. Humans take over when it matters

Asking for a person — or Claude deciding the chat needs one — moves the
conversation to **Waiting** and notifies the team. From the inbox an agent can
**Take over** (the AI stops replying), answer directly, then **Hand back to
AI**. Every switch is recorded as a `ConversationHandoff`. The widget polls for
agent replies, so the customer sees them without a page reload. While a chat is
waiting for a person the AI stays quiet and tells the customer a consultant is
coming. Managers assign or reassign chats from the inbox or the conversation
page; the agent is notified.

Under **AI Settings**, *handoff wait* and *agent idle* timers (minutes, 0 = never)
let the AI take a chat back when nobody picks up a handoff, or when the agent
leaves the customer unanswered. They are checked whenever the customer writes or
the widget polls; `conversations.services.auto_resume_stale_conversations()` does
the same sweep for a scheduler.

### 4. The CRM works the lead

Leads move through **New → Qualified → Interested → Payment Pending →
Converted / Follow-up / Lost**, as a table or a kanban board. Every status
change, assignment and note is written to an activity timeline.

Each lead carries a 0–100 **score** — points for reachable contact details,
known dates, a stated budget and pipeline position — so the pipeline sorts
itself. Follow-up tasks bucket into overdue / today / upcoming with reminders.

Employees only see leads assigned to them or unassigned; managers and admins
see everything.

### 5. Inventory decides what can be sold

Hotels (with room offers), car rentals and tour packages, all hung off a shared
`Destination` list. One search — [`inventory/selectors.py:search_inventory`](inventory/selectors.py) —
serves the dashboard search page, the public API and the AI engine, so the chat
can never recommend something the search page wouldn't show.

**Visibility** is deny-by-exception: inventory shows on every website by
default; a rule hides one item from one website, or raises its priority so it
is recommended first there.

### 6. Payment closes the loop

Creating a booking allocates a number (`STA-20260818-0001`), adds tax from
`BOOKING_TAX_PERCENT`, and moves the lead to **Payment Pending**. Raising a
payment order opens Razorpay Checkout; the returned signature is verified with
`HMAC-SHA256(order_id|payment_id, key_secret)` before anything is marked paid.
A verified payment confirms the booking and moves the lead to **Converted**.

Webhooks are verified against the raw request body. `payment.captured` and
`order.paid` confirm the booking only when the captured amount and currency match
the order; `payment.failed` never downgrades a confirmed booking; `refund.processed`
marks the booking **Refunded**. Each `X-Razorpay-Event-Id` is applied once, so
Razorpay's retries cannot double-apply an event.

**Without Razorpay keys** the gateway runs in simulation mode: orders are minted
locally with the same shape and the same signature scheme, and a *"Complete
simulated payment"* button on the booking page exercises the real verification
path. Configure keys and the same code goes live.

### 7. Reporting

The overview gives KPIs, a lead sparkline, pipeline bars and recent activity,
filterable by website and period. Reports cover leads by source and status,
bookings by product, revenue by day and top destinations, with CSV export.
Analytics compares **website performance** (leads, conversion, revenue per
brand) and **employee performance** (leads handled, conversion, open
follow-ups, revenue).

---

## Roles

| Role | Can do |
|---|---|
| **Admin** | Everything, plus users, teams and the audit log |
| **Manager** | Everything operational: all leads, reports, analytics, refunds, cancellations |
| **Employee** | Own and unassigned leads, chats, bookings, customers |
| **Inventory** | Inventory and visibility; not the CRM pipeline |

Sensitive actions (user changes, key issuance, status changes, payments) are
written to the audit log with actor and IP.

---

## Layout

Each app follows the same shape — `models` → `selectors` (reads) → `services`
(writes and side effects) → `views` / `urls_api`. Business rules live in
`services.py`, never in views, so the dashboard and the API share one code path.

| App | Responsibility |
|---|---|
| `accounts` | Users, roles, teams, audit log |
| `websites` | Brand registry, widget keys |
| `inventory` | Hotels, cars, packages, destinations, visibility, search |
| `crm` | Customers, leads, notes, activities, follow-ups |
| `conversations` | AI engine, chat, handoff, recommendations |
| `bookings` | Bookings, Razorpay payments, notifications |
| `dashboard` | KPIs, reports, analytics |
| `core` | Shared mixins, forms, notifications, audit, API envelope |

---

## API

All responses share an envelope: `{"success": bool, "message": str, "data": ...}`;
list endpoints put `results`, `count`, `page` inside `data`. Errors return
`{"success": false, "error": {...}}`.

Session-authenticated unless noted.

```
GET  /api/v1/health/                          public
GET  /api/v1/users/ · /teams/
GET  /api/v1/websites/ · /websites/{id}/
GET  /api/v1/inventory/{destinations,hotels,cars,packages,visibility}/
GET  /api/v1/inventory/search/?destination=&inventory_type=&max_price=&website=
GET  /api/v1/crm/{customers,leads,follow-ups}/ · /crm/leads/pipeline/
GET  /api/v1/conversations/inbox/ · /{id}/
POST /api/v1/conversations/{id}/handoff/       {"action": "take_over|resume_ai|close"}
POST /api/v1/conversations/widget/chat/        public — widget key
GET  /api/v1/conversations/widget/history/     public — key + session; ?since=<id> to poll
POST /api/v1/conversations/widget/book/        public — "Book this" on a card
POST /api/v1/pay/{token}/order/ · verify/ · simulate/   public — payment link token
GET  /api/v1/bookings/ · /bookings/{id}/
POST /api/v1/bookings/payments/create/         {"booking_id": 1}
POST /api/v1/bookings/payments/verify/         Razorpay checkout response
POST /api/v1/bookings/payments/webhook/        public — HMAC verified
GET  /api/v1/dashboard/{overview,notifications,reports,analytics}/
POST /api/v1/dashboard/notifications/          mark read
```

---

## Configuration

All settings come from `.env` (see `.env.example`).

| Variable | Effect when unset |
|---|---|
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | Fallback when no key is saved under AI Settings; with neither, chat runs on the rule-based engine |
| `AI_MODEL` | Claude model used until an admin saves AI Settings; defaults to `claude-opus-5-5` |
| `AI_ENABLED` | Set `false` to force the rule engine even with a key |
| `RAZORPAY_KEY_ID` / `_SECRET` | Payments run in simulation mode |
| `RAZORPAY_WEBHOOK_SECRET` | Simulation mode: webhooks verified against the simulation secret. With live keys: every webhook is rejected |
| `BOOKING_TAX_PERCENT` | Defaults to 5 |
| `PAYMENT_LINK_TTL_DAYS` | Customer payment links last 7 days |
| `THROTTLE_WIDGET_CHAT` / `_POLL` / `_BOOK`, `THROTTLE_PUBLIC_PAY` | Per-IP limits: `30/minute`, `120/minute`, `20/hour`, `60/hour` |
| `NUM_PROXIES` | `1` (nginx in front). Set `0` when nothing sits in front of gunicorn, or clients could dodge rate limits with a forged `X-Forwarded-For` |
| `DATABASE_URL` | SQLite; set a `postgres://` URL for Postgres |

Before deploying: set `DEBUG=False`, a real `SECRET_KEY` and `ALLOWED_HOSTS`,
run `collectstatic`, and serve through `gunicorn config.wsgi`.

---

## Docker

```bash
cp .env.example .env     # set DOMAIN, CERTBOT_EMAIL, SECRET_KEY, DEBUG=False
docker compose build
./docker/init-letsencrypt.sh   # once, issues the TLS certificate
docker compose up -d
```

Three containers: gunicorn, nginx (TLS + `/static` + `/media`) and certbot for
automatic renewal. Migrations and `collectstatic` run on every boot, so
deploying an update is `git pull && docker compose up -d --build`.

Pushing to `master` deploys automatically: the GitHub Actions workflow in
[.github/workflows/deploy.yml](.github/workflows/deploy.yml) runs the tests,
then rolls the VPS onto the new commit — rolling back if it fails its health
check.

Full VPS walkthrough — DNS, firewall, CD secrets, backups, troubleshooting — in
[DEPLOY.md](DEPLOY.md).

---

## Tests

```bash
python manage.py test
```

155+ tests covering requirement extraction, the Claude layer and each of its
fallbacks (stubbed — no network), lead capture and deduplication, handoff,
pipeline transitions, scoring, role-based visibility, inventory search and
visibility rules, payment signature verification, webhook idempotency, and the
API envelope.
