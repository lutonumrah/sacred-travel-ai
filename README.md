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

**Website enquiry forms.** A site's own contact form can post to
`POST /api/v1/crm/intake/` with the same public key (the website page shows a
ready-made HTML form and `fetch` snippet). Each submission becomes a
**Website Form** lead for that site, reuses the customer if the email or phone
is known, alerts the managers and answers only with a reference such as
`ENQ-000123` (searchable on the leads page). Rate limited per IP.

**Source attribution.** The widget sends the page's UTM tags
(`utm_source/medium/campaign/term/content`), `document.referrer` and the page
URL with the first message; the enquiry form snippet does the same. They are
validated, stored on the conversation (first touch wins), copied to the lead
and on to any booking, shown on the lead and booking pages, and reported by
campaign.

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

**Knowledge base (guardrails).** Managers keep policies, cancellation and
payment terms, FAQs and travel information under **Knowledge Base**, per
website or for all of them. Active articles for the chat's website (its own
first, then shared ones, policies before tips; capped at ~6,000 characters) go
into the model's instructions as *Business information you may use*, and the
model is told to answer policy questions only from it — if the answer is not
there, it says so and offers a consultant, and never invents a policy. Without
a model, the rule engine answers a question that clearly matches an article's
title or keywords with that article.

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

Asking for a person — or the AI model deciding the chat needs one — moves the
conversation to **Waiting** and notifies the chat's assignee, or every manager if
it has none. An agent opens the chat from the inbox and can **Take over** (the
AI stops replying), answer directly (**Send reply** also takes over), then
**Hand back to AI**. Every switch is recorded as a `ConversationHandoff`. The widget polls for
agent replies, so the customer sees them without a page reload. While a chat is
waiting for a person the AI stays quiet and tells the customer a consultant is
coming. Managers assign or reassign chats from the inbox or the conversation
page; the agent is notified. The inbox refreshes itself every 10 seconds
(same filters and page), the conversation page streams in new messages and
status changes every few seconds, and the sidebar's waiting count stays
current — plain polling that pauses while the tab is hidden.

Under **AI Settings**, *handoff wait* and *agent idle* timers (minutes, 0 = never)
let the AI take a chat back when nobody picks up a handoff, or when the agent
leaves the customer unanswered. They are checked whenever the customer writes or
the widget polls, and the scheduler sweeps every minute
(`conversations.services.auto_resume_stale_conversations()`) so a chat the
customer has gone quiet on is handed back too.

### 4. The CRM works the lead

Leads move through **New → Qualified → Interested → Payment Pending →
Converted / Follow-up / Lost**, as a table or a kanban board. On the board,
drag a card to another column (or use the status menu on the card); it is
saved at once and snaps back if the server refuses. The board takes the same
assignee, team, website and source filters as the list, and a column's
"+N more" opens the filtered list. Every status change, assignment and note is
written to an activity timeline.

Each lead carries a 0–100 **score** — a rule-based heuristic, not AI: points
for reachable contact details, a destination, known dates, a stated budget, a
party of more than one and pipeline position (`crm/services.py:score_lead`). Follow-up tasks bucket into overdue / today / upcoming. When a task's
reminder time (or, without one, its due time) passes, the scheduler reminds the
assignee — or the lead's owner, or the managers if nobody owns it — once, in
the app and by email.

Employees only see leads assigned to them, to a team they belong to (with or
without a named owner), or to nobody at all; managers and admins see
everything. Assigning a lead to a team notifies its members. Follow-ups can be edited; moving the reminder later
sends it again. Managers can archive a customer (hidden from lists and
pickers, history kept) and restore them.

### 5. Inventory decides what can be sold

Hotels (with room offers), car rentals and tour packages, all hung off a shared
`Destination` list. A hotel's nightly price is its cheapest active room offer
valid for every night of the stay (tonight when no dates are known), else its
base price — one rule ([`inventory/selectors.py:hotel_nightly_price`](inventory/selectors.py))
used by search, max-price filters, the AI's quotes and every booking price.
Archived items (soft delete, restorable) never reach search, the AI or booking;
a booked item can never be hard-deleted. One search — [`inventory/selectors.py:search_inventory`](inventory/selectors.py) —
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
the order; `payment.failed` marks a pending booking **Failed** (the customer can retry on
the same link) and never downgrades a confirmed one; `refund.processed` for a
full refund marks the booking **Refunded** and the lead **Lost**, while a
partial refund leaves the booking confirmed and alerts managers. Each
`X-Razorpay-Event-Id` is applied once, so Razorpay's retries cannot
double-apply an event.

**Refunds and cancellations.** There is no refund button: refunds are issued in
the Razorpay dashboard and arrive here by webhook. Managers can cancel a booking
that is not yet confirmed (**Cancel booking**), which closes its open orders,
moves a Payment Pending lead back to Interested and emails the customer.

**Without Razorpay keys** the gateway runs in simulation mode: orders are minted
locally with the same shape and the same signature scheme. After **Create
payment order**, managers get a *"Complete simulated payment"* button on the
booking page, and the customer pay page shows *"Simulate payment (test mode)"*;
both exercise the real verification path. Configure both keys and the same code
goes live, and the simulation buttons disappear.

**Customer emails.** When a booking is raised — by the customer in the chat or
by staff — the customer gets *"Your booking … — complete your payment"* with
the summary and their `/pay/<token>/` link; then *Booking confirmed* (with the
Razorpay payment reference) once paid, and a short notice on cancellation or
refund. Staff can re-issue a link and email it from the booking page. Every
send is listed under *Customer emails* on the booking, in the lead timeline and
in the audit log. Emails carry the website's brand name; a mail failure is
logged and never blocks the booking or payment.

**Notifications and email.** Leads, handoffs, payments and follow-up reminders raise in-app notifications
(the bell). Staff with **Email notifications** ticked (default on; each user
changes it under *My account*, admins on the user form) also get them by
email. Two kinds stay in-app only because they are frequent: each new customer
message in a chat a human is handling, and routine pipeline moves of a lead
assigned to you (closing a lead as Converted/Lost is still emailed to
managers). Forgotten passwords are reset from the login page by email.

### 7. Reporting

The overview gives KPIs, a lead sparkline, pipeline bars and recent activity,
filterable by website and period. Reports cover leads by source and status,
bookings by product, revenue by day, top destinations, a conversion funnel
(chats → leads → qualified → bookings → paid → revenue) by website and source,
and a by-campaign breakdown — all filterable by website and by period or an
explicit From/To range. CSV exports (leads, bookings, revenue by
day/website/product, conversion funnel with campaign) honour the same filters.
Analytics compares **website performance** (chats, chat→lead rate, leads,
conversion, bookings, paid, revenue per brand) and **employee performance**
(leads handled, conversion, open follow-ups, chats, handoffs taken, average
first response on taken-over chats, revenue). First response is measured from
when the customer started waiting — the handoff request or their first
unanswered message — to that person's first reply.

---

## Roles

| Role | Can do | Cannot do |
|---|---|---|
| **Admin** | Everything a manager can, plus **Users & Roles** (users, teams, audit log) and **AI Settings** | — |
| **Manager** | All leads, customers, chats and bookings; assign chats; cancel bookings; complete simulated payments; archive/restore customers; websites and widget keys; inventory; **Knowledge Base**; **Reports**, **Analytics** and CSV exports | Users, teams, audit log, AI Settings. Refunds are not done in the app (Razorpay dashboard, then webhook) |
| **Employee** | Leads assigned to them, to their teams, or to nobody; their own and unassigned chats (take over, reply, hand back, book for the customer); bookings they raised or whose lead they can see; matching customers and follow-ups; view inventory | Assign chats, cancel bookings, simulated payments, archive customers, websites and widget keys, edit inventory, reports/analytics/exports, Knowledge Base |
| **Inventory** | Hotels, room offers, cars, packages, destinations, visibility rules, archive/restore/delete | Websites, CRM, conversations, bookings, reports |

The dashboard overview follows the role: business-wide for managers and admins,
*My figures* (only records they can open) for employees, an inventory summary
for the inventory role. Buttons and menu links are shown from the same role
check the target view runs ([`core/access.py:can_open`](core/access.py)), and
form drop-downs only list records the user can see. Everyone gets their own
notifications. Other employees' records are a 404, in the dashboard
and the API alike. Superusers pass every role check and get the manager
alerts; give them the Admin role too so the users list reads correctly. Sensitive actions (user changes, key
issuance, status changes, payments) are written to the audit log with actor
and IP.

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

Session-authenticated unless noted; the role needed is in brackets
(*sales* = admin, manager or employee; employees only get their own records).

```
GET  /api/v1/health/                                   public
GET  /api/v1/users/ · /users/{id}/                     [admin]
GET  /api/v1/teams/                                    [any signed-in user]
GET  /api/v1/websites/ · /websites/{id}/               [manager]
GET  /api/v1/inventory/{destinations,hotels,cars,packages,visibility}/   [any signed-in user]
GET  /api/v1/inventory/search/?q=&destination=&inventory_type=&min_price=&max_price=&website=&limit=
GET  /api/v1/crm/customers/ · /customers/{id}/         [sales]
GET  /api/v1/crm/leads/ · /leads/{id}/ · /leads/pipeline/   [sales] — leads takes the leads-page filters
GET  /api/v1/crm/follow-ups/?bucket=overdue|today|upcoming|completed   [sales]
GET  /api/v1/conversations/inbox/ · /{id}/             [sales] — inbox excludes closed chats
POST /api/v1/conversations/{id}/handoff/               [sales] {"action": "take_over|resume_ai|close"}
POST /api/v1/conversations/widget/chat/                public — widget key
GET  /api/v1/conversations/widget/history/             public — key + session; ?since=<id> to poll
POST /api/v1/conversations/widget/book/                public — "Book this" on a card
POST /api/v1/crm/intake/                               public — website enquiry form, widget key
POST /api/v1/pay/{token}/order/ · verify/ · simulate/  public — payment link token
GET  /api/v1/bookings/ · /bookings/{id}/               [sales]
POST /api/v1/bookings/payments/create/                 [sales] {"booking_id": 1}
POST /api/v1/bookings/payments/verify/                 [sales] Razorpay checkout response
POST /api/v1/bookings/payments/webhook/                public — HMAC verified
GET  /api/v1/dashboard/overview/ · /notifications/     [any signed-in user]
POST /api/v1/dashboard/notifications/                  mark read: {"id": n}, or all without id
GET  /api/v1/dashboard/reports/ · /analytics/          [manager] — website, days, date_from, date_to
```

---

## Configuration

All settings come from `.env` (see `.env.example`).

| Variable | Effect when unset |
|---|---|
| `DEBUG` | Off. With it off, `SECRET_KEY` must be set or the app refuses to start |
| `SECRET_KEY` | Required in production; a dev-only key is used only when `DEBUG=True` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` — hostnames only |
| `CSRF_TRUSTED_ORIGINS` | Empty; list every `https://host` the dashboard is served from or form POSTs fail |
| `USE_X_FORWARDED_PROTO` | Off. Set `True` behind TLS-terminating nginx (also makes session/CSRF cookies Secure) |
| `TIME_ZONE` | `Asia/Kolkata` — drives "today", report dates and the backup hour |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | Fallback when no key is saved under AI Settings; with neither, chat runs on the rule-based engine |
| `AI_MODEL` | Claude model used until an admin saves AI Settings; defaults to `claude-opus-5-5` |
| `AI_ENABLED` | Set `false` to force the rule engine even with a key |
| `AI_MAX_HISTORY` / `AI_TIMEOUT_SECONDS` | 20 previous messages sent to the model; 30 s timeout |
| `RAZORPAY_KEY_ID` / `_SECRET` | Payments run in simulation mode. Live mode needs both (and the `razorpay` package, which is in `requirements.txt`); with only `RAZORPAY_KEY_ID` set, checkout signatures are refused and nothing can be paid |
| `RAZORPAY_WEBHOOK_SECRET` | Simulation mode: webhooks verified against the simulation secret. With live keys: every webhook is rejected |
| `BOOKING_TAX_PERCENT` | Defaults to 5 |
| `PAYMENT_LINK_TTL_DAYS` | Customer payment links last 7 days |
| `THROTTLE_WIDGET_CHAT` / `_POLL` / `_BOOK`, `THROTTLE_PUBLIC_PAY`, `THROTTLE_PUBLIC_INTAKE` | Per-IP limits: `30/minute`, `120/minute`, `20/hour`, `60/hour`, `20/hour` |
| `NUM_PROXIES` | `1` (nginx in front). Set `0` when nothing sits in front of gunicorn, or clients could dodge rate limits with a forged `X-Forwarded-For` |
| `DATABASE_URL` | SQLite; set a `postgres://` URL for Postgres (the nightly backup only covers SQLite) |
| `SITE_URL` | Base of links in emails; defaults to `http://localhost:8000` — set it in production |
| `EMAIL_HOST` (+ `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` / `EMAIL_USE_SSL`, `EMAIL_TIMEOUT`) | Emails are printed to the log (console backend) instead of sent. `EMAIL_BACKEND` overrides the choice |
| `DEFAULT_FROM_EMAIL` / `SERVER_EMAIL` | `Scared Travel <no-reply@localhost>` |
| `BACKUP_HOUR` / `BACKUP_KEEP_DAYS` / `BACKUP_ENABLED` / `BACKUP_DIR` | Nightly backup after 03:00, newest 14 kept, on, `backups/` beside the database |
| `LOG_LEVEL` | `INFO` |
| `DOMAIN`, `CERTBOT_EMAIL`, `CERTBOT_STAGING`, `SEED_DEMO`, `COMPOSE_FILE` | Docker only — see [DEPLOY.md](DEPLOY.md) |

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

Four containers: gunicorn, a scheduler (`manage.py run_scheduled_jobs --loop`:
follow-up reminders, chat auto-resume and a nightly SQLite backup to
`/app/data/backups`), nginx (TLS + `/static` + `/media`) and certbot for
automatic renewal. Migrations and `collectstatic` run when `web` boots (the
scheduler skips them via `SKIP_BOOT_TASKS=1`), so deploying an update is
`git pull && docker compose up -d --build`.

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

514 tests (43 of them end-to-end journeys) covering requirement extraction, the Claude and Gemini
layers and each of their fallbacks (stubbed — no network), lead capture and
deduplication, handoff and AI auto-resume, pipeline transitions, scoring,
role-based and record-level visibility, inventory search and visibility rules,
payment signature verification, webhook idempotency and amount checks, the API
envelope, CORS and throttling, customer and staff emails (locmem backend),
password reset, follow-up reminders, the scheduler and the nightly backup.

End-to-end journeys — widget chat to paid booking, enquiry form to follow-up
reminder, human handoff, payment webhook scenarios, AI providers and a security
sweep — live in [`e2e/`](e2e/) and run with the same command.

Cross-browser smoke tests (Playwright: Chromium, Firefox and WebKit, desktop and
mobile) live in [`browser_tests/`](browser_tests/) and run with
`browser_tests/run.sh`, which needs Docker. See
[browser_tests/README.md](browser_tests/README.md).

---

## Documentation

| Document | For |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Staff guide by role: every page, button and metric |
| [docs/UAT_CHECKLIST.md](docs/UAT_CHECKLIST.md) | Client acceptance testing on staging, with sign-off |
| [docs/FIP_TRACEABILITY.md](docs/FIP_TRACEABILITY.md) | Each FIP feature → where it is implemented and which tests cover it |
| [DEPLOY.md](DEPLOY.md) | VPS deployment, CI/CD, backups, troubleshooting and the go-live checklist |
| [browser_tests/README.md](browser_tests/README.md) | Running the cross-browser suite |
