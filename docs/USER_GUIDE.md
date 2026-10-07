# Scared Travel AI — Staff User Guide

This guide is for the people who use the system every day: admins, managers,
sales staff (employees) and inventory staff. It describes what is on each page,
what each button does, and who can use it. Labels in **bold** are the exact
words you will see on screen.

Contents

1. [Roles: who can see and do what](#1-roles-who-can-see-and-do-what)
2. [Signing in, passwords and email alerts](#2-signing-in-passwords-and-email-alerts)
3. [Websites, the chat widget and the enquiry form](#3-websites-the-chat-widget-and-the-enquiry-form)
4. [Inventory](#4-inventory)
5. [Customers, leads and the pipeline](#5-customers-leads-and-the-pipeline)
6. [Follow-ups and reminders](#6-follow-ups-and-reminders)
7. [Live conversation inbox and human takeover](#7-live-conversation-inbox-and-human-takeover)
8. [Bookings and payments](#8-bookings-and-payments)
9. [Dashboard, reports, analytics and CSV exports](#9-dashboard-reports-analytics-and-csv-exports)
10. [AI Settings and the Knowledge Base](#10-ai-settings-and-the-knowledge-base)
11. [Users, teams and the audit log](#11-users-teams-and-the-audit-log)
12. [Notifications](#12-notifications)
13. [FAQ and troubleshooting](#13-faq-and-troubleshooting)

---

## 1. Roles: who can see and do what

Every user has one role, set by an admin under **Users & Roles**. The sidebar
only shows the menus your role can open; if you follow a link to a page you
are not allowed on, you are sent to the dashboard with *"You do not have
permission to open that page."*

| Area | Admin | Manager | Employee | Inventory User |
|---|---|---|---|---|
| **Dashboard** overview | Business-wide | Business-wide | *My figures* | Inventory overview |
| **Websites** — view list, embed snippets, keys | Yes | Yes | No | No |
| **Websites** — add, edit, issue/revoke keys, archive | Yes | Yes | No | No |
| **Inventory** — view hotels, cars, packages, destinations, visibility, search | Yes | Yes | Yes (view only) | Yes |
| **Inventory** — add, edit, enable/disable, room offers, visibility rules, archive / restore / delete | Yes | Yes | No | Yes |
| **CRM** (Customers, Leads, Pipeline, Follow-ups) | All records | All records | See "What employees see" below | No access |
| Archive / restore a customer | Yes | Yes | No | No |
| **Conversations** (inbox, history, widget preview) | All chats | All chats | Own and unassigned chats | No access |
| Assign a chat to someone | Yes | Yes | No | No |
| **Bookings** and **Payments** | All | All | See below | No access |
| Cancel a booking | Yes | Yes | No | No |
| **Complete simulated payment** (test mode only) | Yes | Yes | No | No |
| **Reports**, **Analytics**, CSV exports | Yes | Yes | No | No |
| **Knowledge Base** | Yes | Yes | No | No |
| **Users & Roles**, **Teams**, **Audit log** | Yes | No | No | No |
| **AI Settings** | Yes | No | No | No |
| **Notifications**, **My Account** | Own | Own | Own | Own |

**What employees see.** An employee works only the records that are theirs:

- **Leads**: leads assigned to them, leads assigned to a team they belong to
  (with or without a named owner), and completely unassigned leads (no person
  and no team). A lead given only to a team is seen by that team's members, not
  by other employees.
- **Follow-ups**: tasks assigned to them, plus tasks on any lead they can see.
- **Customers**: customers linked to a lead, chat or booking they can see, plus
  customers with no history at all (so they can open a profile they have just
  created).
- **Conversations**: chats assigned to them and unassigned chats (which includes
  chats waiting for a human that nobody has been given yet). Team membership
  does not apply to chats.
- **Bookings and payments**: bookings they created, and bookings whose lead they
  can see.

Anything outside that scope is a "page not found" for them, including through
the API. Managers and admins see everything.

**Things everyone should know about visibility**

- The **Dashboard** overview is business-wide for managers and admins. An
  employee's overview is labelled **My figures**: every number (leads, revenue,
  bookings, live chats, overdue follow-ups, the pipeline bars) counts only the
  records they can open. The Inventory User gets an inventory overview instead
  (see section 9).
- Buttons and menu links only appear when your role can use them: **Export
  CSV**, **Cancel booking**, **Complete simulated payment**, **Assign** on a
  chat, the Websites menu and the inventory add/edit/archive buttons are hidden
  from roles that cannot use them. They are worked out from the same check the
  page itself makes, so a visible button always works.
- Drop-down lists in forms only offer records you can see: the **Customer**
  list on a lead, the **Lead** list on a follow-up and the customer / lead lists
  on a booking.
- A user marked inactive (**Active** unticked) cannot sign in. A user with
  **Is active employee** unticked can still sign in, but is no longer offered
  for assignment, is left out of manager alerts and does not appear in the
  employee analytics.
- An account created on the server with `createsuperuser` passes every page
  check and receives the manager alerts (new leads, handoffs, payments), but its
  **Role** still shows *Employee* until an admin changes it. Set it to Admin so
  the users list reads correctly.

---

## 2. Signing in, passwords and email alerts

### Signing in and out

Open the site and you land on **Sign in**. Enter **Username** and **Password**
and press **Login**. **Logout** is at the bottom of the sidebar.

### Forgotten password

1. On the sign-in page, click **Forgot password?**
2. Enter the email address on your account and press **Send reset link**.
3. You always see **Check your email** — the page looks the same whether or not
   the address is known, so it cannot be used to discover accounts. Inactive
   users get no email.
4. Open the link in the email (*"Reset your … password"*), enter **New password**
   and **Confirm new password**, and press **Set password**.
5. A link that has been used or has expired shows **Link no longer valid**;
   choose **Send a new link**. Reset links expire after 3 days (Django's
   default).

Admins cannot type a new password for another user on the user form; the user
resets it themselves with **Forgot password?**. Make sure every user has an
email address.

### My Account: password and email alerts

Click your username at the bottom of the sidebar to open **My Account**.

- **Change password** — old password, new password twice, then **Change password**.
- **Email notifications** — your **Email** address (where reset links and alerts
  go) and the **Email notifications** tick box, then **Save**. With it ticked you
  get an email for each lead, handoff, payment and follow-up alert, as well as
  seeing it under **Notifications**. Two kinds are never emailed because they
  are frequent: each new customer message in a chat you are handling, and
  routine pipeline moves of a lead assigned to you. See
  [Notifications](#12-notifications) for the full list.

---

## 3. Websites, the chat widget and the enquiry form

Menu: **Websites** (page title *Website / Brand Management*) — managers and
admins only. Employees and inventory users do not see the menu: they have no
need for widget keys or embed snippets.

Each customer-facing website or brand is registered here. The website's
**Source identifier** tags every chat, lead and booking that comes from it, and
is what the per-website reports group by.

### Adding or editing a website (managers and admins)

**Add website** opens the form:

| Field | Meaning |
|---|---|
| **Name** | Internal name |
| **Brand name** | Shown to customers in the widget title, payment page and emails (falls back to Name) |
| **Domain** | Hostname only, e.g. `scaredtravel.com`. The widget and enquiry form only work on pages served from this domain, `www.` of it, or any subdomain of it, over http or https. If you include a port (`localhost:3000`) it must match exactly. |
| **Source identifier** | Short slug, e.g. `scared-main` |
| **Logo** | Shown on the customer payment page |
| **Primary color** | Widget and email accent colour (a hex value such as `#0F766E`) |
| **Widget enabled** | Untick to switch the chat widget off for this site |
| **Is active** | Untick to switch the site off: its widget key and enquiry form stop working |
| **Notes** | Internal notes |

Press **Save website**. A new website is issued a widget key automatically
(*"… registered — a widget key was issued."*).

### The website page

Click a website name to open it. You see counts of **Leads**, **Conversations**
and **Bookings**, whether the **Widget** is On or Off, and:

- **Embed the chat widget** — the snippet to paste before `</body>` on the
  website. It looks like:

  ```html
  <script
    src="https://your-host/static/js/widget.js"
    data-scared-key="pk_..."
    data-scared-api="https://your-host"
    data-scared-color="#0F766E"
    data-scared-title="Your Brand"
    defer></script>
  ```

  **Preview the widget** opens the widget preview for this website.
- **Website enquiry form** — a ready-made HTML form plus a small script for the
  site's developer. Each submission becomes a **Website Form** lead for this
  website. The visitor sees only a reference such as `ENQ-000123`, which you can
  type into the leads **Search** box to find the lead. Email or phone is
  required. UTM tags, the referring page and the landing page are recorded
  automatically.
- **API keys** — the keys for this site. **Issue new key** adds another active
  key; **Revoke** switches one off at once (any site still using it stops
  working). The snippets always show the newest active key, so after issuing a
  new key, re-copy the snippets; the old key keeps working until you revoke it.
- **Danger zone → Archive website** — hides the website everywhere and stops its
  widget, but keeps its leads, chats and bookings.

### Trying the widget

**Conversations → Widget preview** (button at the top of the inbox) shows the
real widget for any active website with a key. Choose the **Website** from the
menu. Everything you type creates a genuine conversation and lead. Suggestions
on the page: *"I want a hotel in Goa for 4 people, budget 40k, 12/03/2027"*,
then *"I want to talk to a human"* to see a handoff arrive in the inbox.

### What the customer sees in the widget

A chat bubble in the bottom-right corner. Inside: a message box (*"Ask about
your trip…"*), the AI's replies, and recommendation cards with a **Book this**
button. Booking asks for the dates, number of travellers and any missing name,
email or phone, then **Confirm and get payment link**; the chat then shows the
booking with a **Pay now** button. On a phone the open chat fills the screen.
The chat survives a page reload, and while a consultant is involved or a payment
is outstanding the widget checks for new messages every few seconds.

---

## 4. Inventory

Menu: **Inventory** → **Hotels**, **Cars**, **Packages**, **Destinations**,
**Visibility**, **Search**. Everyone can view these pages; only admins,
managers and inventory users see the add/edit buttons.

Inventory is what the AI is allowed to recommend and what customers can book
from the chat. Only **active**, non-archived items are recommended.

### Destinations

**Add destination**: **Name**, **Code** (unique slug), **City**, **State**,
**Country**, **Is active** → **Save destination**. The AI recognises a
customer's destination only if it matches a destination recorded here, so add
every place you sell. A destination you no longer sell can be **archived** (see
*Archiving and deleting* below) once no live hotel, car or package uses it.

### Hotels and room offers

**Add hotel**: **Name**, **Destination**, **Star rating**, **Address**,
**Description**, **Amenities** (comma separated, e.g. `Wi-Fi, Pool, Breakfast`),
**Check in time**, **Check out time**, **Base price**, **Currency**, **Image**,
**Is active** → **Save hotel**.

After saving, open the hotel again with **Edit** to see **Room offers**. **Add an
offer** (**Title**, **Room type**, **Price** per night, **Currency**, **Valid
from**, **Valid to**, **Inclusions**, **Is active**) → **Add offer**. Each offer
can be edited (**Edit** → **Save offer**) or deleted (**Remove**). Leave a
validity date blank for "open-ended".

**How a hotel's nightly price is worked out.** One rule is used everywhere —
inventory search, the AI's quotes and recommendation cards, the max-price
filter, chat bookings and the staff booking form:

1. Take the hotel's **active** room offers whose validity window covers **every
   night** of the stay (check-in to the night before check-out). When the dates
   are not known yet — e.g. the customer has not given them to the chat — the
   offers valid **tonight** are used.
2. The **cheapest** of those sets the nightly price, and its room type is shown
   next to the hotel (e.g. *4-star · Deluxe*).
3. If no offer applies, the hotel's **Base price** is the nightly price.

The **Hotels** list shows both the **Base price** and **Tonight** (the price
the rule gives today). A booking keeps a copy of the offer it was priced with
(title, room type, inclusions), so editing or removing the offer later does not
change existing bookings.

### Cars

**Add car**: **Name**, **Destination**, **Vehicle type** (e.g. Sedan, SUV,
Tempo Traveller), **Brand**, **Model name**, **Seats**, **Transmission**,
**Fuel type**, **Daily price**, **Currency**, **Description**, **Image**,
**Is active** → **Save vehicle**. A car is never offered to a party larger than
its seats. Vehicle type and transmission are matched against what the customer
asks for (e.g. "SUV", "automatic").

### Packages

**Add package**: **Name**, **Destination**, **Duration days**, **Duration
nights**, **Base price** (per person), **Currency**, **Inclusions**,
**Exclusions**, **Description**, **Image**, **Is active**, **Itinerary** (one
line per day — the day number is added automatically) → **Save package**.

### Enabling, disabling and the ID number

Each list has **Disable** / **Enable** buttons. Disabled items stay in the list
(status *Inactive*) but are never recommended or bookable from the chat. The
small `#12` under each name is the item's ID, used by visibility rules and the
staff booking form.

### Archiving and deleting

Admins, managers and inventory users see an **Archive** button on every hotel,
car, package and destination. Archiving:

- switches the item off and removes it from the lists, inventory search, the AI
  and booking (an open chat recommendation for it answers *"Sorry, this option
  is no longer available."*);
- keeps it in the database with its bookings and history intact.

Tick **Include archived** (or choose status **Archived only**) on a list to see
archived items, marked *Archived*. Each has **Restore** — it comes back
*Inactive*, so press **Enable** when it is ready to sell again — and **Delete
permanently**. Permanent deletion is refused, with a message, when:

- the item has any booking (it stays archived for the booking history);
- a destination is still set on any hotel, car or package, archived ones
  included.

A destination cannot be archived while a live (non-archived) hotel, car or
package uses it — archive or move those first. Archived destinations disappear
from the destination drop-downs and the AI no longer recognises them.

### Visibility rules (per website)

Menu: **Inventory → Visibility**. By default every active item shows on every
website. A rule changes that for one item on one website:

- **Is visible** unticked → the item is hidden on that website (search, AI
  recommendations and chat booking).
- **Is visible** ticked with a **Priority** above 0 → the item is pushed to the
  top of that website's search and AI recommendations (higher first).

**Add or update a rule**: **Website**, **Inventory type**, **Object id** (the
item's `#` number), **Is visible**, **Priority** → **Save rule**. Saving a rule
for the same website and item again updates it. Rules cannot be deleted from
the dashboard; to undo a hide rule, save it again with **Is visible** ticked and
priority 0. Filter the **Rules** table by **Website** and **Type**.

### Search

Menu: **Inventory → Search**. Fields: **Keyword**, **Type**, **Destination**
(name, city or code), **Check in**, **Check out**, **Min price**, **Max price**,
**Website** → **Search**. Hotels are priced for the dates given (tonight if
none), using the room-offer rule above; the price filter uses that same price,
and each hotel result names the offer it was priced with. The API takes the
same dates as `check_in` / `check_out` and returns the `offer` with each hotel.
This is exactly the search the AI uses: choosing a website applies its
visibility rules. Results are sorted by website priority, then price.

---

## 5. Customers, leads and the pipeline

Menu: **CRM** → **Customers**, **Leads**, **Pipeline**, **Follow-ups**
(admins, managers and employees).

### Customers

**Customer Profiles** lists customers you can see. Filters: **Search** (name,
email, phone, WhatsApp), **City**, and — for managers — **Include archived**.

**Add customer**: **First name**, **Last name**, **Email**, **Phone**,
**Whatsapp**, **City**, **Country**, **Preferred language**, **Notes**,
**Is active** → **Save customer**. An email or a phone is required.

Customers are also created automatically when a chat or an enquiry form gives
an email or phone. If that email or phone already belongs to a customer, the
existing customer is reused rather than duplicated.

A customer page shows their **Leads**, **Bookings** and **Conversations**, with
**Edit**, **New lead** and, for managers, **Archive**.

**Archive (managers and admins).** Archiving hides a customer from customer
lists and from the customer pickers on forms; their leads, chats and bookings
are kept. To bring one back, tick **Include archived**, open the customer and
press **Restore customer**.

### Where leads come from

| Source | How |
|---|---|
| **AI Chat** | Created automatically as soon as a chat reveals a destination or an email/phone. Later messages update the same lead. |
| **Website Form** | Each enquiry-form submission (see [Websites](#3-websites-the-chat-widget-and-the-enquiry-form)). The visitor's message is added as a note. |
| **Manual**, **Phone**, **Email**, **Other** | Created by staff with **Add lead**. |

A new lead with no owner alerts every manager; a lead created with an owner
alerts that person; a lead created for a team alerts the team's members.

### Lead statuses

| Status | Meaning |
|---|---|
| **New** | Just arrived |
| **Qualified** | Destination, travel dates, party size and a contact are all known |
| **Interested** | The customer picked a specific option |
| **Payment Pending** | A booking has been raised and is awaiting payment |
| **Converted** | Paid — set automatically when a payment is verified |
| **Follow-up** | Parked for a later conversation (set by staff) |
| **Lost** | Not going ahead; you can record a reason |

**Automatic moves.** The system moves leads forward by itself, and never
backwards, never out of Converted or Lost:

- **New → Qualified** when destination, dates (or a travel month), an explicit
  number of travellers and an email or phone are all known — from chat or the
  enquiry form.
- **→ Interested** when the customer (or an agent for them) books a
  recommendation from the chat.
- **→ Payment Pending** when a booking is raised for the lead.
- **→ Converted** when the booking's payment is verified (this one always
  applies).
- Cancelling a pending booking moves a **Payment Pending** lead back to
  **Interested** (if it has no other open booking). A full refund moves a
  **Converted** or **Payment Pending** lead to **Lost** with the reason
  *"Booking … refunded"* (if it has no other paid booking).

Staff can set any status by hand.

**Score (0–100).** A simple points score, not AI: phone/WhatsApp 15, email 10,
destination 10, travel date 15, budget 15, more than one traveller 5, plus
pipeline position (Qualified/Follow-up 10, Interested 20, Payment Pending and
Converted 30). It is recalculated on every change and shown on lists and cards.

### The leads list

**Leads** shows a table with filters: **Search** (title, destination, customer
name, email or phone, or an enquiry reference such as `ENQ-000123`), **Status**,
**Source**, **Destination**, **Assigned to**, **Team**, **Website**,
**Created from**, **Created to** → **Filter** / **Reset**. Buttons: **Pipeline
view**, **Export CSV** (managers; exports all leads — see
[CSV exports](#csv-exports)), **Add lead**.

**Add lead** / **Edit**: **Title**, **Customer**, **Website**, **Status**,
**Source**, **Destination**, **Travel start**, **Travel end**, **Travelers
count**, **Budget min**, **Budget max**, **Assigned to**, **Assigned team** →
**Save lead**.

### The pipeline board

**Pipeline** shows one column per status, newest 25 cards per column, with a
count badge. A column with more shows **+N more**, which opens the leads list
filtered to that status. Filters: **Source**, **Assigned to**, **Team**,
**Website**. **Table view** goes back to the list with the same filters.

To move a lead:

- **Desktop**: drag the card to another column.
- **Phone, tablet or keyboard**: use the status menu on the card.

The move is saved immediately. Moving to **Lost** asks *"Why was this lead
lost? (optional)"* — Cancel leaves the card where it was. If the server refuses
(for example the lead was reassigned away from you meanwhile) the card snaps
back and a message explains why.

### The lead page

- **Trip request** — customer, destination, travel dates, travellers, budget,
  source and website, **Reference** (`ENQ-…`), score, lost reason and any
  preferences the chat picked up (hotel stars, amenities, food, car type,
  transmission, trip style).
- **Notes** — type in the box and press **Add note**. Notes are only ever seen
  by staff; the **Is internal** tick just adds an *Internal* badge.
- **Activity** — the timeline: creation, every status change (manual and
  automatic), assignments, notes, follow-ups and reminders, and customer emails
  about bookings.
- **Move stage** — **Status** and **Lost reason** → **Update status**.
- **Assignment** — **Assigned to** (a person or *Unassigned*) and **Assigned
  team** (a team or *No team*) → **Assign**. Anyone who can see the lead can
  reassign it. The new owner is notified; assigning a team notifies the team's
  members. If you hand a lead to someone else and can no longer see it, you are
  returned to the leads list.
- **Follow-ups** — the lead's tasks and a **Schedule one** form.
- **Chats**, **Bookings** — linked records.
- **Source attribution** — website and the UTM tags, referrer and landing page
  that brought the customer.
- **Create booking** (top right) opens the staff booking form. It is not
  pre-filled from the lead; pick the customer and lead on the form.

---

## 6. Follow-ups and reminders

Menu: **CRM → Follow-ups** (page title *Follow-up Tasks*).

Tasks are grouped as **Overdue**, **Due today** (your business's calendar day)
and **Upcoming**; **Show completed** adds **Recently completed**.

**Schedule a follow-up** (on this page or on a lead's page): **Lead**,
**Assigned to**, **Title**, **Due at**, **Reminder at** (optional, must be on or
before the due time), **Notes** → **Schedule**. The assignee is notified.

Each open task has **Edit** (title, assignee, due, reminder, notes → **Save
follow-up**) and **Done** (or **Mark done** on the lead page).

**How reminders work.** Every minute the scheduler checks for open tasks whose
reminder time — or due time, if no reminder is set — has passed. Each task is
reminded **once**, to the task's assignee; if it has none (or they are
inactive), the lead's owner; if nobody, all managers. The reminder appears under
**Notifications** and is emailed to those with email alerts on. The title says
*Reminder:* or *Overdue:*. It is also written to the lead's activity.

If you edit a task and move its reminder (or due time, when there is no
reminder) **later**, the reminder will fire again at the new time. Moving it
earlier does not resend one that already went out.

The dashboard's **Reminders due** panel lists your own open tasks whose
reminder time has passed.

---

## 7. Live conversation inbox and human takeover

Menu: **Conversations** (admins, managers, employees). The red number next to
it is the count of chats waiting for a human that you can open.

### Inbox

**Live Conversation Inbox** lists every chat that is not closed. Filters:
**Search** (session code, customer name or email, or any text in the messages),
**Status** (**AI Active**, **Human Active**, **Waiting**, **Closed**),
**Website** → **Filter** / **Reset**. A yellow banner shows how many chats are
waiting for a human agent.

The table refreshes itself every 10 seconds (keeping your filters and page) and
pauses while the browser tab is hidden. Managers and admins see an assignee menu
on each row; choosing a person assigns the chat at once. Buttons at the top:
**Full history** (every chat including closed ones, same filters) and **Widget
preview**. Click a chat (or **Open**) to work it.

### Chat statuses

| Status | Meaning |
|---|---|
| **AI Active** | The AI is answering |
| **Waiting** | A human has been asked for; the AI stays quiet and tells the customer a consultant has been asked to join |
| **Human Active** | A person has taken over; the AI does not reply |
| **Closed** | Finished; no further replies from the dashboard |

A chat moves to **Waiting** when the customer asks for a person (phrases such
as "talk to a human", "agent", "call me", "manager", "complaint", "refund",
"cancel my booking") or when the AI model decides a person should step in (an
upset customer, a refund or cancellation, or a question it cannot answer from
the inventory or the Knowledge Base). The assigned agent — or, if the chat is
unassigned, every manager — is notified.

### The conversation page

- **Transcript** — every message, labelled *Customer*, *AI assistant* (with the
  engine that answered: `claude`, `gemini` or `rules`), the agent's username, or
  *System*. New messages and status changes appear by themselves every few
  seconds.
- **Take over** — you become the chat's owner and the AI stops replying. The
  customer sees *"… joined the chat."* (shown when the chat is AI Active or
  Waiting).
- **Send reply** — type in the box. Replying also takes the chat over if you had
  not already.
- **Hand back to AI** — the AI resumes and the customer sees *"AI assistant
  resumed this conversation."* You stay the chat's assignee.
- **Close** — closes the chat (asks for confirmation). Closed chats leave the
  inbox but stay in **Full history**.
- **Assignment** (managers and admins) — **Assign to** → **Save assignment**.
  The person is notified. Only active admins, managers and employees can be
  picked.
- **What the AI learned** — the extracted requirements, preferences and contact
  details.
- **Linked records** — website, customer, lead (with its status), session code
  and start time.
- **Recommended** — every option the AI offered, with **Book this for the
  customer** (see [Bookings](#booking-for-the-customer-from-a-chat)).
- **Bookings from this chat**, **Handoffs** (who took over and when the AI
  resumed) and **Source attribution**.

Taking over a chat assigns it to you, so other employees no longer see it in
their inbox; managers still do.

### AI auto-resume

So a customer is never left waiting forever, an admin can set two timers under
**AI Settings** (0 = never):

- **Resume AI if no one picks up a handoff within (minutes)** — default 15. If a
  chat stays **Waiting** that long, the AI takes it back and tells the customer:
  *"Sorry — all our travel consultants are busy right now. I'm back to help in
  the meantime, and a consultant will still follow up with you."*
- **Resume AI if the agent leaves the customer waiting for (minutes)** — default
  0 (off). Counted from the customer's oldest message that the agent has not
  answered since their last reply or takeover.

The timers are checked whenever the customer writes or the widget polls, and
the scheduler sweeps every minute. The assignee (or the managers) is notified.

---

## 8. Bookings and payments

Menu: **Bookings** (admins, managers, employees). **Payments** is a button on
the bookings page.

### Booking statuses

| Status | Meaning |
|---|---|
| **Pending Payment** | Raised, waiting for the customer to pay |
| **Confirmed** | Payment verified |
| **Failed** | Razorpay reported a failed attempt; the customer can try again (see *When a payment fails*) |
| **Cancelled** | Cancelled by a manager before payment |
| **Refunded** | Fully refunded in Razorpay |

There are no *Draft* or *Paid* statuses: a booking is raised ready to pay and is
**Confirmed** the moment its payment is verified. (Any old rows in those states
were moved to Pending Payment and Confirmed respectively when this release was
installed.)

Every booking has a number like `STA-20261007-0001`. Tax is added on top of the
subtotal at the `BOOKING_TAX_PERCENT` rate (5% unless your administrator changed
it).

### How a booking is raised

**1. The customer books from the chat.** On a recommendation card the customer
presses **Book this**, confirms dates and travellers and any missing contact
details, and presses **Confirm and get payment link**. The server prices the
item itself — the browser cannot change the price:

- Hotel: the nightly price × nights (check-out minus check-in, at least one).
  The nightly price is the cheapest active room offer valid for every night of
  the stay, else the hotel's base price (see *How a hotel's nightly price is
  worked out* in section 4). The room type is shown on the booking, the payment
  page, the widget's booking card and the customer emails.
- Car: daily price × days, counting both pick-up and drop-off day; the car must
  seat every traveller.
- Package: base price per person × travellers; the end date follows from the
  package length.

Past dates, stays or rentals over 60 days, more than 50 travellers and items
with no price are refused with a readable message. The booking is raised as
**Pending Payment**, the customer gets a payment link in the chat and by email,
the lead moves to **Payment Pending**, and managers plus the chat's and lead's
owners are notified. Pressing **Book this** again for the same option, dates and
party size returns the existing pending booking rather than a duplicate.

#### Booking for the customer from a chat

On the conversation page, under **Recommended**, open **Book this for the
customer**, enter **Check-in**/**Check-out** (hotel), **Pick-up**/**Drop-off**
(car) or **Start date** (package), and **Travellers**, then **Create booking**.
It uses the same pricing as the widget. The chat must already have a customer
(an email or phone captured); otherwise you see *"Capture the customer's email
or phone in the chat before booking."* The payment link is emailed to the
customer if they have an email address, and you are taken to the booking page.

**2. Staff create a booking by hand.** **Bookings → Create booking**:
**Customer**, **Website**, **Lead** (optional), **Product type**, **Product id**
(the inventory `#` number — find it on **Inventory → Search** or the inventory
lists), **Product name** (filled from the item if left blank), **Travel start**,
**Travel end**, **Travelers count**, **Room offer** (hotels), **Currency**,
**Subtotal**, **Email the payment link to the customer** → **Create booking**.

- **Room offer** lists every active offer as *Hotel (#id) · room type · price
  per night*. Pick one to book that room type, or leave *Cheapest offer valid
  for the dates* to let the pricing rule choose. An offer for a different hotel,
  or one not valid for every night, is refused.
- Leave **Subtotal** blank and it is priced from inventory exactly as a chat
  booking is (and the dates must then be valid). Type a subtotal to charge a
  negotiated amount instead; the chosen room offer is still recorded.

Tax is added automatically. A payment link is created straight away.

### The booking page

- **Booking summary** — number, customer, product, **Room** (hotel bookings priced
  with a room offer: room type, offer title and inclusions), dates, travellers,
  website, lead, chat (**Open transcript**), who created it.
- **Amount** — Subtotal, Tax, Total.
- **Payments** — every payment attempt with its Razorpay order and payment IDs,
  status and paid time. A **Simulation mode** badge shows when no live Razorpay
  keys are configured.
- **Customer payment link** (Pending Payment or Failed bookings only):
  - The current link with its expiry, and **Copy customer payment link**.
  - **Issue new link & email customer** / **Issue new link only** (or **Issue new
    link** when the customer has no email). Issuing a new link immediately
    stops the old one working. Links last `PAYMENT_LINK_TTL_DAYS` days (7 by
    default).
- **Customer emails** — each email sent (or failed) for this booking: payment
  link, booking confirmation, cancellation notice, refund notice.
- **Cancel** (managers and admins; not shown once a booking is Confirmed,
  Cancelled or Refunded) — optional **Reason**, then **Cancel booking**.
- **Source attribution**.

### The customer payment page

The link (`/pay/<token>/`) needs no login. It shows the booking number, room
(for hotels), name, dates, travellers, the price breakdown and a **Pay ₹…**
button that opens Razorpay Checkout. An expired link shows *"This payment link has expired"* and
no prices or personal details; issue a new one from the booking page. After
payment the page shows *"Payment received — your booking is confirmed."*

### Taking payment from the dashboard

On a Pending Payment booking without an open order, **Create payment order**
opens a Razorpay order (on a Failed booking the button reads **Retry payment
(new order)**). Then:

- **Live mode**: **Pay with Razorpay** opens Razorpay Checkout in your browser
  (useful when taking a payment over the phone).
- **Simulation mode** (no live keys): managers and admins see **Complete
  simulated payment**, which signs and verifies a payment locally through the
  same verification code. Employees see a note instead: the customer can
  complete the simulated payment from their payment link. The customer payment
  page shows **Simulate payment (test mode)** instead of the pay button. Both
  disappear as soon as live Razorpay keys are configured.

### When a payment fails

When Razorpay reports a failed attempt the booking becomes **Failed** and
managers are notified. Nothing is lost — the booking can still be paid:

- **The customer** opens the same payment link: the page says *"The last payment
  attempt did not go through. No money was taken for it. You can try again
  below."* and the pay button opens a **new** Razorpay order. If a card is
  declined while Checkout is open, the page says so and the button can be
  pressed again.
- **Staff** see a warning with the failure reason on the booking page and can
  press **Retry payment (new order)**, or **Issue new link** (with or without
  email) if the customer's link has expired.

Opening the new order puts the booking back to **Pending Payment**; the failed
attempt stays in the **Payments** table for the record. Confirmed, Cancelled and
Refunded bookings cannot open a new order.

### How a payment is confirmed

The booking is only marked **Confirmed** after the server has checked it:

- The signature Razorpay Checkout returns is verified, and in live mode the
  payment is fetched from Razorpay to confirm the amount, currency and that it
  was captured.
- Razorpay also notifies the server directly (webhooks): *payment captured* and
  *order paid* confirm the booking if the amount and currency match; a failed
  payment marks a pending booking **Failed** but never downgrades a confirmed
  one. Each Razorpay event is applied only once.

On confirmation: the lead moves to **Converted**, the chat (if any) shows
*"Payment received — booking … confirmed."*, the customer is emailed *Booking
confirmed* with the Razorpay payment reference, and the booking's creator and
the managers are notified. If the amount does not match, the booking is **not**
confirmed and managers receive *"Payment amount mismatch on …"*.

### Cancellations and refunds

- **Cancelling** (managers and admins) is for bookings not yet paid. It closes
  any open payment order so a late payment cannot confirm it, moves a
  **Payment Pending** lead back to **Interested**, emails the customer a
  cancellation notice, and notifies managers, the creator and the lead owner.
  If a customer pays an order after cancellation, managers are told *"… refund
  it in Razorpay."*
- **Refunds are not done in this system.** There is no refund button. Issue the
  refund in the **Razorpay dashboard**. When Razorpay reports it (the
  `refund.processed` webhook):
  - a **full** refund marks the payment and booking **Refunded**, moves the lead
    to **Lost**, emails the customer a refund notice and notifies managers;
  - a **partial** refund leaves the booking **Confirmed**, emails the customer a
    refund notice for the refunded amount and notifies managers.
- A confirmed booking cannot be cancelled from the dashboard; refund it in
  Razorpay and it becomes **Refunded** automatically.

### Payments page

**Bookings → Payments** lists payment attempts you can see with filters
**Search** (order ID, payment ID, booking number) and **Status**. The **Settled
revenue** tile is the total of all successful payments in the system, and
**Gateway** shows **Live** or **Simulation**.

---

## 9. Dashboard, reports, analytics and CSV exports

### Dashboard overview (everyone)

What the overview shows depends on your role:

- **Managers and admins** — business-wide figures, as described below.
- **Employees** — the same tiles, labelled **My figures**: each number counts
  only the leads, chats, bookings, payments and follow-ups you can open (see
  *What employees see* in section 1). *Revenue* reads **My revenue**.
- **Inventory users** — an inventory overview instead: active / inactive /
  archived counts for hotels, cars, packages and destinations; **Hidden items**
  (inactive stock, plus how many items are hidden on a website by a visibility
  rule); **Room offers valid today**; and the most **Recently updated** items.
  No CRM or revenue figures.

The API `GET /api/v1/dashboard/overview/` returns the same figures for the
caller, with a `scope` field (`business`, `mine` or `inventory`). Passing
`?scope=` for more than your role allows (an employee asking for `business`, an
inventory user for `mine` or `business`) is refused with 403.

Filters at the top (managers, admins and employees): website (**All websites**
or one) and period (**Last 7 days**, **Last 30 days**, **Last 90 days**).

| Tile / panel | What it counts |
|---|---|
| **Leads** | Leads created in the period. *"N still new"*: of those, how many are still New. |
| **Converted** | Of the leads created in the period, how many are now Converted; *% conversion* = converted ÷ leads. |
| **Live chats** | Chats not closed right now (not limited to the period). *"N waiting for a human"*: chats in Waiting right now. |
| **Bookings** | Bookings created in the period; *confirmed* = of those, how many are Confirmed; *pending* = all bookings currently Pending Payment, whenever created. |
| **Revenue** | Sum of successful payments whose payment date falls in the period. Refunded payments drop out. *Avg … per booking*: average total of all Confirmed bookings, any date. |
| **Overdue follow-ups** | Open follow-ups past their due time (an employee's: those they can see). |
| **Leads per day** | Leads created each day of the period. |
| **Pipeline** | All leads (any date) by current status. |
| **Recent leads / Live conversations / Recent bookings** | The latest 10 you can open. |
| **Reminders due** | Your own open follow-ups whose reminder time has passed. |
| **Live inventory** | Active hotels, cars and packages. |

The website filter applies to the tiles, Leads per day and Pipeline, but not
to Overdue follow-ups, Live inventory, the three *Recent* lists or Reminders
due.

### Reports (managers and admins)

Menu: **Reports**. Filters: **Website**, **Period** (**Last 7 days**, **Last 30
days** — default, **Last 90 days**, **Last year**), **From**, **To** → **Apply**
/ **Reset**. A From/To range overrides the period; the line under the filters
says which dates are shown.

| Section | What it shows |
|---|---|
| **Leads**, **Converted**, **Revenue** tiles | As on the dashboard, for the chosen dates and website |
| **Bookings** tile | Bookings created in the chosen dates |
| **Leads by source** | Leads created in the dates, by source |
| **Leads by status** | Leads created in the dates, by their current status |
| **Bookings by product** | Bookings created in the dates by product type, with the summed booking totals of **all** of them (including pending and cancelled) — this column is booked value, not money received |
| **Top destinations** | The 10 destinations most asked for by leads created in the dates |
| **Conversion funnel by website and source** | See below |
| **By campaign** | The same funnel grouped by UTM source / medium / campaign |
| **Revenue by day** | Successful payments by payment date |

**How the conversion funnel is counted.** It follows the leads *created* in the
chosen dates (a cohort) and whatever they went on to do, whenever it happened:

- **Chats**: chats started in the dates. Only AI Chat rows have chats; other
  sources show "—".
- **Leads**: leads created in the dates.
- **Qualified**: of those leads, how many are — or at any point were —
  Qualified, Interested, Payment Pending or Converted.
- **Bookings**: all bookings raised for those leads.
- **Paid**: of those bookings, how many are Confirmed.
- **Revenue**: successful payments on those bookings.
- **Lead → paid**: paid bookings ÷ leads, as a percentage.

### Analytics (managers and admins)

Menu: **Analytics** (page title *Website & Employee Analytics*). Same period and
From/To filters (default 90 days); no website filter.

**Website performance** — one row per website:

| Column | Meaning |
|---|---|
| **Chats** | Chats started in the period |
| **Chats → lead** | % of those chats that produced a lead |
| **Leads** | Leads created in the period |
| **Converted**, **Rate** | Of those leads, how many are now Converted, and the % |
| **Bookings**, **Paid** | Bookings created in the period, and how many of them are Confirmed |
| **Revenue** | Successful payments with a payment date in the period |

**Employee performance** — one row per active user who can be assigned work
(admins, managers and employees):

| Column | Meaning |
|---|---|
| **Leads**, **Converted**, **Rate** | Leads assigned to them that were created in the period, how many are Converted, and the % |
| **Open follow-ups** | Their open follow-ups right now (any date) |
| **Chats** | Chats assigned to them that started in the period |
| **Handoffs taken** | Times they took over a chat in the period |
| **Avg first response** | For each chat they took over in the period: from when the customer started waiting (the handoff request or their first unanswered message, whichever came first) to that person's first reply. Takeovers they never replied to are left out; the number of chats measured is shown underneath. |
| **Revenue** | Successful payments in the period on bookings whose lead is assigned to them |

**Where customers want to go** — the top destinations across all websites.

### CSV exports

On **Reports**, **Leads CSV**, **Bookings CSV**, **Revenue CSV** and
**Conversion CSV** download a file using the filters currently applied on the
reports page (website and dates).

| File | Contents |
|---|---|
| Leads | Reference, ID, Title, Customer, Status, Source, Website, Destination, Assigned to, Team, Score, Created, UTM fields, Referrer, Landing page. All leads unless a period or From/To is chosen. Up to 5,000 rows. |
| Bookings | Number, Customer, Product, Product type, Website, Lead, Status, Total, Currency, Created, utm_source/medium/campaign. All bookings unless a period or From/To is chosen. Up to 5,000 rows. |
| Revenue | Successful payments by Date, Website and Product type: Payments, Amount, Currency. Always for the chosen dates (last 30 days by default). |
| Conversion | The funnel grouped by website, source and campaign, with Chat to lead % and Lead to paid %. Always for the chosen dates. |

The **Export CSV** buttons on the **Leads** and **Bookings** lists download all
leads / all bookings; the filters on those list pages are not applied to the
file.

---

## 10. AI Settings and the Knowledge Base

### How the AI works

Every customer message goes through two layers:

1. **Rules (always).** The system reads the destination (only destinations you
   have recorded), product type, dates, number of travellers, budget, contact
   details and preferences from the message, adds them to what it already knows
   from earlier messages, and searches your live inventory for that website,
   honouring visibility rules, star rating, seats, car type and transmission.
2. **Claude or Gemini (optional).** If a provider and key are configured, the
   model writes the reply. It is given only the inventory the rules found, the
   Knowledge Base articles for that website, and strict instructions. It can
   only pick from that inventory, so it cannot invent a hotel, price or
   availability. If the model fails for any reason (no key, network error, bad
   answer, a refusal), the customer still gets the rule-based reply. The
   transcript shows which engine answered each message.

Without a model, the rule-based replies list matching options with prices and
ask for whatever is still missing (destination, dates, travellers, budget).

### AI Settings (admins)

Menu: **AI Settings**. The top panel, **Currently answering chats**, shows **AI
on** with the provider and model, or **Rule-based**, and who last changed the
settings.

| Field | Meaning |
|---|---|
| **Let AI write chat replies** | Off, or with no key, the chat uses the rule-based replies (leads are still captured) |
| **Provider** | **Anthropic (Claude)** or **Google (Gemini)** |
| **Model** | Pick from the list for the chosen provider |
| **Other model ID** | Optional; overrides the list (e.g. a newly released model) |
| **Anthropic API key** / **Gemini API key** | Paste a key to save it. Saved keys are never shown again — only a masked hint. Leave blank to keep the saved one. |
| **Remove saved Anthropic key** / **Remove saved Gemini key** | Tick to delete a saved key |
| **Resume AI if no one picks up a handoff within (minutes)** | See [auto-resume](#ai-auto-resume). 0 = never. |
| **Resume AI if the agent leaves the customer waiting for (minutes)** | See [auto-resume](#ai-auto-resume). 0 = never. |

Press **Save settings**. If AI is on but there is no key for the chosen
provider, you get a warning and chats stay rule-based. A key saved here takes
precedence over one in the server's environment. Changes are written to the
audit log without the key itself.

**Test connection** checks the saved key and model with the provider without
generating anything (no cost) and reports, for example, *"Connected — … is
available."*, *"Anthropic rejected the API key."* or *"… has no model called
…"*.

### Knowledge Base (managers and admins)

Menu: **Knowledge Base**. These are the business facts the AI may use:
policies, cancellation and payment terms, FAQs and travel information.

**The guardrail:** the AI answers policy, cancellation, refund, payment,
document and other FAQ questions **only** from active articles. If the answer
is not in an article, it says it does not have that information and offers a
consultant; it never invents a policy, fee or deadline. So add your
cancellation, refund and payment policies first.

**Add article**: **Title**, **Category** (Policy, Cancellation, Payment, FAQ,
Travel information, Other), **Website** (one website, or **All websites**),
**Keywords** (comma-separated words customers use, e.g. "refund, cancel, money
back"), **Content** (write it as you would tell a customer, with exact fees,
deadlines and conditions), **Is active** → **Save article**. **Edit** and
**Delete** are on each row. Changes apply to the very next chat message.

What the AI receives for a chat: the active articles for that chat's website
first, then the ones for all websites; within those, Policy, Cancellation,
Payment, FAQ, Travel information, Other; up to about 6,000 characters in total
(anything beyond is left out, so keep articles concise).

Without an AI model, the rule-based replies answer a question that clearly
matches an article's keywords or most of its title words with (the start of)
that article.

Note that a customer typing "refund" or "cancel my booking" is also handed to a
person, even if an article answers the question.

---

## 11. Users, teams and the audit log

All under **Users & Roles** (admins only).

### Users

The list has filters **Search**, **Role** and **Status** (Active / Inactive).
**Add user**: **Username**, **First name**, **Last name**, **Email address**,
**Phone**, **Role** (Admin, Manager, Employee, Inventory User), **Is active
employee**, **Email notifications**, **Avatar**, **Password**, **Password
confirmation** → **Save user**. **Edit** additionally has **Active**.

- To stop someone signing in, untick **Active**.
- To keep them able to sign in but take them out of assignment pickers, manager
  alerts and analytics, untick **Is active employee**.
- There is no delete button; deactivate instead, so their history stays
  attributed.

### Teams

**Teams** (button on the users page): **Add team** → **Name**, **Description**,
**Members**, **Is active** → **Save team**. Teams are used to assign leads to a
group: every member can see the team's leads (and their follow-ups and
bookings), even when the lead's named owner is someone else, and members are
notified when a lead is assigned to the team. Only active teams appear in
pickers. Teams do not apply to chats.

### Audit log

**Audit log** (button on the users page) lists sensitive actions, newest first,
50 per page: **When**, **Actor** (or *system*), **Action**, **Entity**, **IP**
and **Detail**. It records, among others: user, team and preference changes;
password reset emails and resets; website changes and widget key issue/revoke;
inventory changes and visibility rules; lead creation, edits, status changes and
assignments; follow-ups; customer archive/restore; chat handoffs, takeovers,
resumes, closes and assignments; Knowledge Base and AI Settings changes;
bookings, payment orders, verifications (including failures and amount
mismatches), cancellations, refunds, payment links (the link itself is never
logged) and every customer email.

---

## 12. Notifications

**Notifications** in the sidebar shows a count of unread alerts (the *My
Account* page calls this "the bell"). The page has **Unread only** / **Show
all**, **Mark all read**, and **Mark read** per alert; click an alert's title to
open the lead, chat or booking. Each alert is tagged Lead, Handoff, Payment or
Follow-up.

| Alert | Who gets it | Emailed? |
|---|---|---|
| New lead (no owner) | Managers and admins | Yes |
| New lead with an owner / lead assigned to you | The owner | Yes |
| Lead assigned to a team | Team members | Yes |
| Lead closed as Converted or Lost | Managers and admins | Yes |
| Other status moves of your lead | You | No (in-app only) |
| Follow-up assigned to you | You | Yes |
| Follow-up reminder / overdue | Assignee, else lead owner, else managers | Yes |
| Chat waiting for a human | The chat's assignee, else managers | Yes |
| Chat assigned to you | You | Yes |
| New customer message in a chat you handle | You | No (in-app only) |
| AI took back an unanswered handoff / idle chat | The chat's assignee, else managers | Yes |
| New booking | Managers and admins (not the person who raised it) | Yes |
| Chat booking | The chat's and the lead's owners | Yes |
| Payment received / booking confirmed | Booking creator; managers | Yes |
| Payment failed, amount mismatch, payment after cancellation | Managers | Yes |
| Booking cancelled, refunded, partially refunded | Managers, plus creator and lead owner where applicable | Yes |

"Yes" means emailed to people who have **Email notifications** ticked and an
email address on their account.

---

## 13. FAQ and troubleshooting

**The chat widget does not appear on our website.**
Check on the website page that the **Widget** shows On (the website is active
and **Widget enabled** is ticked) and that the key in the snippet is listed as
Active under **API keys**. The page must be served from the website's registered
**Domain** (or `www.`/a subdomain of it); anywhere else the widget silently
fails. If the domain uses a non-standard port, include it in the domain.

**The enquiry form says "Unknown or inactive website key."**
The key was revoked or the website is inactive or archived. Re-copy the snippet
from the website page.

**A customer asked something and the AI said it doesn't know.**
The answer is probably not in the Knowledge Base. Add or activate an article for
that website or for **All websites**.

**The AI never recommends a particular hotel.**
Check that it is active and not archived, has a destination the customer named,
a price (a base price or a room offer valid for the dates), and is not hidden
for that website under **Visibility**. With a budget, the hotel's nightly price
for the customer's dates (the cheapest valid room offer, else the base price)
must be within it. Use **Inventory →
Search** with the same destination and website: if it does not appear there,
the AI will not offer it.

**Chats show `rules` as the engine although we configured Claude/Gemini.**
Open **AI Settings**: check **Let AI write chat replies** is ticked and a key is
saved for the selected provider, then press **Test connection**. Any model
failure falls back to rule-based replies for that message.

**A chat is stuck in Waiting.**
Open it and press **Take over**, or ask a manager to assign it. If the handoff
timer is set, the AI takes it back automatically after that many minutes.

**I can't see a lead / chat / booking a colleague mentioned.**
Employees only see their own, their teams' and unassigned leads, and their own
and unassigned chats. Ask a manager to assign it to you.

**The customer says the payment link doesn't work.**
Links expire after `PAYMENT_LINK_TTL_DAYS` (7 days by default), and issuing a
new link kills the old one. Open the booking and use **Issue new link & email
customer**, or copy the link and send it another way.

**The customer paid but the booking is still Pending Payment.**
Check **Payments** on the booking. In live mode the booking is confirmed by the
checkout callback or by Razorpay's webhook; if the webhook is not set up and the
customer closed the page before the callback, it stays pending. Your
administrator should check the webhook (see DEPLOY.md, *Go-live checklist*). An
amount mismatch also leaves it pending and alerts managers.

**How do I refund a customer?**
In the Razorpay dashboard. The booking updates itself when Razorpay reports the
refund. There is no refund button here.

**I don't receive alert emails.**
Check **My Account → Email notifications** is ticked and your email address is
correct. If nobody receives mail, the server's email settings need attention.

**I didn't get the password reset email.**
Check spam. If still nothing, ask an admin to confirm the email address on your
account under **Users & Roles**.

**The pipeline card jumped back to where it was.**
The server refused the move — usually because the lead is no longer visible to
you. The message shown explains why. Reload the page.

**A closed chat: can the customer continue?**
Yes. When the customer writes into a closed chat it reopens: it goes back to
**AI Active**, the AI answers, and the chat reappears in the inbox (an internal
note *"The customer returned; conversation reopened."* marks the moment).
