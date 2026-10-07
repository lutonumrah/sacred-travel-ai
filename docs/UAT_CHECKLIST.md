# Client UAT Checklist

User acceptance testing on **staging**, grouped by FIP phase. Each line is one
action and the result you should see. Tick it when it behaves as described; if
not, write the item number and what happened in the *Issues* table at the end.

Labels in **bold** are the exact words on screen. Full explanations of each
feature are in [USER_GUIDE.md](USER_GUIDE.md).

## Before you start

- [ ] 0.1 Staging runs the build to be accepted. Note the commit / date here: ______________
- [ ] 0.2 You have five test accounts, one per role, with real mailboxes you can read: an **Admin**, a **Manager**, two **Employees** (call them *Agent A* and *Agent B*) and an **Inventory User**. Agent A and the Manager are both members of one team (e.g. *Sales*); Agent B is not.
- [ ] 0.3 At least one website is registered, with an active widget key, and its domain points at a test page where the widget snippet is installed (or use **Conversations → Widget preview**).
- [ ] 0.4 Note whether payments are in **Simulation** or **Live (test keys)** mode: **Bookings → Payments → Gateway** tile. Simulation: ______ Live test keys: ______
- [ ] 0.5 Note whether an AI provider is configured: **AI Settings** shows **AI on** or **Rule-based**. ______

---

## P1 — Foundation: sign-in, passwords, roles

- [ ] 1.1 Open the site while signed out → you are sent to **Sign in**.
- [ ] 1.2 Sign in with a wrong password → *"Your username and password did not match. Please try again."*
- [ ] 1.3 Sign in as Admin → the dashboard opens; the sidebar shows **Dashboard, Websites, Inventory, CRM, Conversations, Bookings, Notifications, Reports, Analytics, Knowledge Base, Users & Roles, AI Settings**.
- [ ] 1.4 Sign in as Manager → same sidebar without **Users & Roles** and **AI Settings**.
- [ ] 1.5 Sign in as Employee → sidebar shows **Dashboard, Inventory, CRM, Conversations, Bookings, Notifications** only (no **Websites**).
- [ ] 1.6 Sign in as Inventory User → sidebar shows **Dashboard, Inventory, Notifications** only.
- [ ] 1.7 As Employee, type `/dashboard/reports/` into the address bar → back on the dashboard with *"You do not have permission to open that page."*
- [ ] 1.8 As Inventory User, type `/crm/leads/` → same permission message.
- [ ] 1.9 **Logout** (bottom of the sidebar) → back on **Sign in**; opening `/dashboard/` again asks you to sign in.
- [ ] 1.10 Click your username → **My Account**. Change your password with **Change password** → *"Your password has been changed…"*. Sign out and in with the new password.
- [ ] 1.11 Signed out, click **Forgot password?**, enter your account email, **Send reset link** → **Check your email** page. An email *"Reset your … password"* arrives; its link opens on the staging address (not `localhost`).
- [ ] 1.12 Use the link: **New password**, **Confirm new password**, **Set password** → **Password set**. Sign in with it.
- [ ] 1.13 Open the same reset link again → **Link no longer valid**.
- [ ] 1.14 Request a reset for an address that has no account → the same **Check your email** page; no email arrives.
- [ ] 1.15 Open `/api/v1/health/` → JSON containing `"status": "ok"` and `"database": "ok"`.
- [ ] 1.16 Signed out, open `/api/v1/crm/leads/` → an error response (not lead data).

## P2 — Websites and inventory

### Websites

- [ ] 2.1 As Manager, **Websites → Add website**: fill **Name**, **Brand name**, **Domain** (e.g. `uat-site.example`), **Source identifier**, **Primary color** → **Save website** → *"… registered — a widget key was issued."*; the website page shows an **Active** key under **API keys**.
- [ ] 2.2 The website page shows **Embed the chat widget** with a `<script …widget.js…>` snippet containing that key and colour, and **Website enquiry form** with an HTML form snippet.
- [ ] 2.3 **Issue new key** → a second key appears; the snippets now show the new key. **Revoke** the old key → its status is **Revoked**.
- [ ] 2.4 Edit the website, untick **Widget enabled**, save → the **Widget** tile says **Off**; the widget on the test page no longer answers. Tick it again.
- [ ] 2.5 As Employee (and as Inventory User), there is no **Websites** menu; typing `/websites/` into the address bar → the permission message. On **Conversations → Widget preview** there is no **Embed snippet** link.
- [ ] 2.6 Archive a throw-away website (**Archive website**, confirm) → it disappears from the list; its leads remain in **CRM → Leads**.

### Inventory

- [ ] 2.7 As Inventory User, **Inventory → Destinations → Add destination** (e.g. *Udaipur*, code `udaipur`) → it appears in the list.
- [ ] 2.8 **Hotels → Add hotel**: Udaipur, 4 stars, amenities `Wi-Fi, Pool`, base price 6000 → saved, shows on the list with an ID (`#…`).
- [ ] 2.9 **Edit** the hotel → **Room offers** panel. **Add an offer** (title, room type, price, validity) → **Add offer**; it appears in the table. **Edit** it, change the price, **Save offer**. **Remove** it (confirm) → gone.
- [ ] 2.10 **Cars → Add car**: Udaipur, SUV, 7 seats, automatic, daily price 3500 → saved.
- [ ] 2.11 **Packages → Add package**: Udaipur, 3 days / 2 nights, base price 15000, itinerary of three lines → saved; re-opening shows the three itinerary lines.
- [ ] 2.12 **Disable** the car → status **Inactive**. **Enable** it again → **Active**.
- [ ] 2.13 **Inventory → Search**: Destination `Udaipur` → the hotel, car and package all appear with prices and *per night / per day / per person*.
- [ ] 2.14 Search with **Max price** 5000 → only items priced at or below 5000 appear.
- [ ] 2.15 **Visibility → Add or update a rule**: your website, **Inventory type** Hotel, **Object id** = the hotel's ID, **Is visible** unticked → **Save rule**. Search Udaipur with that **Website** selected → the hotel is missing; with **All websites** → it is there.
- [ ] 2.16 Save the same rule with **Is visible** ticked and **Priority** 10 → with that website selected the hotel is listed first.
- [ ] 2.17 Save a rule with an Object id that does not exist → an error naming the missing ID.
- [ ] 2.18 As Employee, open **Hotels** → list visible, no **Add hotel** / **Edit** / **Archive** buttons.
- [ ] 2.19 *Offer pricing.* On the Udaipur hotel (base price 6000) add offer *Deluxe saver*, room type *Deluxe*, price 4500, no dates. The **Hotels** list shows **Tonight** 4500. **Search** Udaipur → the hotel shows INR 4,500 per night, *4-star · Deluxe* and *Offer: Deluxe saver*; with **Max price** 5000 it is listed.
- [ ] 2.20 Edit the offer so **Valid from** is next month → **Tonight** goes back to 6000; **Search** with **Check in** / **Check out** inside next month shows 4500 again. Add a second offer valid next month at 4000 → that one wins for those dates. Untick **Is active** on it → 4500 again.
- [ ] 2.21 *Archive.* On the car from 2.10 press **Archive** (confirm) → *"… archived. It is hidden from lists, search and the AI …"*; it is gone from **Cars** and **Search**. Tick **Include archived** → it is listed as **Archived** with **Restore** and **Delete permanently**.
- [ ] 2.22 **Restore** it → back in the list as **Inactive**; **Enable** it. Archive and **Delete permanently** a throw-away hotel that was never booked → gone. Try **Delete permanently** on an archived item that has a booking → *"… has 1 booking and cannot be deleted …"*.
- [ ] 2.23 **Destinations → Archive** on Udaipur while it has live hotels/cars/packages → refused (*"… Archive or move them first."*). A destination with nothing on it can be archived and then no longer appears in the hotel/car/package **Destination** menus.

## P3 — CRM: customers, leads, pipeline, follow-ups

- [ ] 3.1 As Agent A, **CRM → Customers → Add customer** with no email and no phone → *"Provide at least an email address or a phone number."* Add one with an email → the customer page opens.
- [ ] 3.2 From the customer page, **New lead**: title, source **Phone**, destination Udaipur, travel dates, 2 travellers, budget → **Save lead** → the lead page shows the details, a **Reference** `ENQ-…` and a score.
- [ ] 3.3 **Leads**: the **Source** filter offers AI Chat, Website Form, Manual, Phone, Email, Other. Filter by **Status**, **Source**, **Destination**, **Assigned to**, **Team**, **Website**, **Created from/to** → each narrows the list; **Reset** clears it.
- [ ] 3.4 Type the lead's `ENQ-…` reference in **Search** → exactly that lead.
- [ ] 3.5 On the lead, **Add note** → it appears under **Notes** with your name and an entry in **Activity**.
- [ ] 3.6 **Move stage** to **Follow-up** → **Update status** → the badge changes; **Activity** shows *"New → Follow-up"*.
- [ ] 3.7 **Pipeline** (desktop): drag the card to **Interested** → it stays there after a page reload.
- [ ] 3.8 **Pipeline** on a phone (or a narrow window): change the status menu on a card → the card moves column and stays after reload.
- [ ] 3.9 Drag a card to **Lost** → a prompt asks *"Why was this lead lost? (optional)"*; enter a reason → the lead page shows **Lost because** with your text. Drag again and press Cancel at the prompt → the card does not move.
- [ ] 3.10 Pipeline filters **Source / Assigned to / Team / Website** narrow the board; **Table view** opens the list with the same filters. A column with more than 25 leads shows **+N more**.
- [ ] 3.11 **Assignment**: assign the lead to Agent B → **Assign**. Agent B gets a notification *"Lead assigned to you: …"* (and an email if alerts are on). Agent A is returned to the leads list and can no longer open the lead.
- [ ] 3.12 As Manager, assign a different lead to the team only (**Assigned to** = *Unassigned*, **Assigned team** = Sales) → Agent A (a member) is notified *"Lead assigned to Sales: …"* and can open it; Agent B (not a member) cannot (page not found).
- [ ] 3.13 As Agent B, open the URL of a lead that belongs to Agent A → page not found.
- [ ] 3.14 Leads with no owner and no team are visible to both agents.
- [ ] 3.15 On a lead, **Schedule one** follow-up due in 5 minutes with a reminder in 2 minutes, assigned to Agent A → it appears under **Follow-ups → Upcoming** (or **Due today**).
- [ ] 3.16 (Needs the scheduler running.) Within about 3 minutes Agent A gets a notification *"Reminder: …"* and an email; the lead's **Activity** shows *"Reminder sent: …"*. It is not sent twice.
- [ ] 3.17 **Edit** the follow-up and move the reminder 2 minutes later → the reminder is sent again at the new time.
- [ ] 3.18 Set a reminder later than the due time → *"The reminder must fire on or before the due date."*
- [ ] 3.19 **Done** on the task → it leaves the open lists; **Show completed** lists it under **Recently completed**.
- [ ] 3.20 A follow-up with no reminder fires as *"Overdue: …"* once its due time passes.
- [ ] 3.21 As Manager, open a customer → **Archive** (confirm) → it disappears from **Customers** and from the **Customer** picker on **Add lead**. Tick **Include archived**, open it, **Restore customer** → back.
- [ ] 3.22 As Employee, a customer page has no **Archive** button.

### Website enquiry form (lead creation from a configured source)

- [ ] 3.23 Submit the enquiry form snippet on the test page (name, email, destination, dates, 2 travellers, message) → the page shows *"Thank you! Your reference is ENQ-……"*.
- [ ] 3.24 A **Website Form** lead with that reference exists for the right website; the message is a note; managers got a *"New lead: …"* notification. Because destination, dates, travellers and contact were all given, its status is **Qualified**.
- [ ] 3.25 Submit again with the same email → no new customer is created; the new lead belongs to the existing customer.
- [ ] 3.26 Submit with neither email nor phone → *"Please give an email address or a phone number."*
- [ ] 3.27 Open the test page with `?utm_source=google&utm_medium=cpc&utm_campaign=uat` and submit → the lead's **Source attribution** panel shows those values and the landing page.

## P4 — AI chat widget

- [ ] 4.1 On the test page, the chat bubble appears bottom-right; it opens with the brand title and colour.
- [ ] 4.2 Type *"Hi"* → a welcome asking where, when and how many.
- [ ] 4.3 Type *"I want a hotel in Udaipur for 2 people from <a date next month> to <3 days later>, budget 20k"* → a reply listing options with real prices from your inventory and recommendation cards with **Book this**.
- [ ] 4.4 The chat never recommends the hotel hidden for this website in 2.15 (re-hide it to test), nor a disabled item.
- [ ] 4.5 Type *"we are 8 people, need an SUV"* → no car with fewer than 8 seats is offered.
- [ ] 4.6 Ask for a destination you do not sell (e.g. *"Antarctica"*) → the AI does not invent a hotel or price.
- [ ] 4.7 In **CRM → Leads**, an **AI Chat** lead exists for this chat (destination Udaipur), linked to the conversation. Further messages update the same lead, not new ones.
- [ ] 4.8 Give an email in the chat → the lead gets a customer with that email. Once destination, dates, party size and contact are known the lead becomes **Qualified** automatically.
- [ ] 4.9 The lead page shows preferences the chat picked up (e.g. *Car type: suv*), and the conversation page shows **What the AI learned**.
- [ ] 4.10 Reload the test page → the chat history and cards are restored.
- [ ] 4.11 On a phone, open the widget → it fills the screen and is usable.
- [ ] 4.12 With an AI provider configured, the conversation transcript labels replies `claude` or `gemini`; without one, `rules`.
- [ ] 4.13 Open the test page with `?utm_campaign=uat-chat` and start a new chat (private window) → the lead and later booking show that campaign under **Source attribution**.

## P5 — Live inbox and human takeover

- [ ] 5.1 In the widget type *"I want to talk to a human"* → the chat replies that a consultant will join. In the dashboard the **Conversations** badge increases, the inbox shows a **Waiting** chat and *"1 chat waiting for a human agent."*; managers get *"Chat waiting for a human"* (in-app and email).
- [ ] 5.2 Leave the inbox open without reloading → new chats and status changes appear within about 10 seconds.
- [ ] 5.3 Inbox filters **Search** (try a word from the customer's message), **Status**, **Website** work; **Full history** includes closed chats.
- [ ] 5.4 As Agent A, open the waiting chat → transcript, **What the AI learned**, **Linked records** (customer, lead with status) are shown. Press **Take over** → *"You are now handling this chat."*; the customer sees *"agent_a joined the chat."* (with the username).
- [ ] 5.5 Type a reply and **Send reply** → it appears in the customer's widget within a few seconds, without reloading.
- [ ] 5.6 The customer replies → it appears on the conversation page within a few seconds; the AI does not answer while the chat is **Human Active**.
- [ ] 5.7 As Agent B, the chat taken by Agent A is no longer in the inbox, and its URL is page not found.
- [ ] 5.8 **Hand back to AI** → the customer sees *"AI assistant resumed this conversation."* and the AI answers the next message.
- [ ] 5.9 As Manager, use the assignee menu on an inbox row to assign a chat to Agent B → Agent B is notified *"Chat assigned to you"*; on the conversation page **Assignment** shows Agent B.
- [ ] 5.10 As Employee, there is no assignee menu in the inbox or **Assignment** panel on the conversation.
- [ ] 5.11 (Needs the scheduler running, or the customer to write again.) **AI Settings**: set **Resume AI if no one picks up a handoff within (minutes)** to 1 → **Save settings**. Request a human in a new chat and wait about 2 minutes → the customer sees *"Sorry — all our travel consultants are busy right now…"* and the chat returns to **AI Active**; managers are notified. Set it back.
- [ ] 5.12 Set **Resume AI if the agent leaves the customer waiting for (minutes)** to 1, take over a chat, let the customer write and do not answer → after about 2 minutes the AI resumes with *"Sorry for the wait — your consultant has stepped away…"*. Set it back to 0.
- [ ] 5.13 **Close** a chat (confirm) → it leaves the inbox; it is listed in **Full history** as **Closed**; the reply box is replaced by *"This conversation is closed."*
- [ ] 5.14 The **Handoffs** panel on a chat lists who took over and when the AI resumed.

## P6 — Booking and payment

- [ ] 6.1 In the widget, press **Book this** on a hotel card → a form asks for **Check-in**, check-out, travellers and any missing name/email/phone. Enter a past date → a readable error. Enter valid dates → **Confirm and get payment link** → the chat shows the booking number, the price breakdown (*"N nights × …"*), tax, total and **Pay now**.
- [ ] 6.2 The customer receives *"Your booking … — complete your payment"* by email with the summary and a link to the staging address.
- [ ] 6.3 In the dashboard: the booking is **Pending Payment**, linked to the lead and chat; the lead is **Payment Pending**; managers got *"New booking …"*. The booking total = subtotal + tax.
- [ ] 6.4 Press **Book this** again with the same details → the same booking number (no duplicate).
- [ ] 6.5 Book a car with more travellers than seats → refused, naming the seat count. Book a package → the end date is set from the package length.
- [ ] 6.6 Open the payment link signed out → the booking summary, price and a pay button, without login.
- [ ] 6.7 *Simulation mode*: press **Simulate payment (test mode)** → *"Payment received — your booking is confirmed."*. *Live test keys*: press **Pay ₹…**, pay with a Razorpay test card → the same confirmation.
- [ ] 6.8 After payment: the booking is **Confirmed** with a payment row (status **Success**, payment ID); the lead is **Converted**; the chat shows *"Payment received — booking … confirmed."*; the customer receives *"Booking confirmed: …"* with the payment reference; managers and the booking's creator are notified.
- [ ] 6.9 Open the payment link again → it shows the booking as confirmed; there is no pay button.
- [ ] 6.10 On a conversation page, under **Recommended**, use **Book this for the customer** (dates, travellers, **Create booking**) → you land on the new booking; *"… the payment link has been emailed to the customer."*
- [ ] 6.11 **Bookings → Create booking** by hand: a customer, product type Hotel, **Product id** = an existing hotel ID, dates, subtotal, **Email the payment link to the customer** ticked → **Create booking** → Pending Payment booking with a payment link; an email is listed under **Customer emails**. With a non-existent product ID → *"No hotel exists with ID …"*.
- [ ] 6.11a Create another hotel booking with **Subtotal** left blank and a **Room offer** chosen → the subtotal is that offer's price × nights; the booking page shows **Room** with the room type and inclusions; the customer's payment page and email show the room. Choosing an offer of a different hotel, or one not valid for the dates, is refused with a message.
- [ ] 6.11b In the widget, book a hotel that has an offer valid for your dates → the booking is priced at the offer price per night (not the base price) and the booking card shows the room type.
- [ ] 6.12 On a pending booking, **Copy customer payment link** copies the link. **Issue new link & email customer** (confirm) → *"New payment link issued — valid until …. The old link no longer works. It has been emailed to the customer."*; the old link now shows *"This payment link has expired"*; the new one works.
- [ ] 6.13 As Manager, **Cancel booking** on a pending booking with a reason → **Cancelled**; the lead goes back to **Interested**; the customer gets *"Booking … cancelled"*. Its payment link can no longer be paid.
- [ ] 6.14 As Employee, a pending booking shows no **Cancel** panel and no **Complete simulated payment** button (in simulation mode a note says the customer can pay from their link); the bookings and leads lists show no **Export CSV**.
- [ ] 6.15 *Simulation mode only*: on a pending booking, **Create payment order** → an order row appears; as Manager **Complete simulated payment** → *"Simulated payment captured — booking confirmed."*
- [ ] 6.16 *Live test keys only*: confirm that **Complete simulated payment** and **Simulate payment (test mode)** no longer appear anywhere.
- [ ] 6.17 Confirm there is no refund button on a confirmed booking (refunds are made in the Razorpay dashboard — see P8).
- [ ] 6.18 **Bookings → Payments** lists the payments with order and payment IDs; the **Gateway** tile matches the mode noted in 0.4.
- [ ] 6.19 *Failed payment, customer retry* (live test keys: pay with a Razorpay failing test card; simulation: ask the developer to send a `payment.failed` test webhook). The booking becomes **Failed** and managers are notified. Open the payment link → *"The last payment attempt did not go through …"* and the pay button. Pay successfully → **Confirmed**; the **Payments** table shows the failed and the successful attempt.
- [ ] 6.20 *Failed payment, staff retry*: on another Failed booking the booking page shows a warning with the failure reason and **Retry payment (new order)** → a new order row appears and the booking is **Pending Payment** again. If its link had expired, **Issue new link & email customer** sends a working one.
- [ ] 6.21 The **Status** filter on **Bookings** offers Pending Payment, Confirmed, Cancelled, Failed and Refunded (no Draft or Paid).

## P7 — Dashboard, notifications, reports, analytics

- [ ] 7.1 **Dashboard**: tiles **Leads**, **Converted**, **Live chats**, **Bookings**, **Revenue**, **Overdue follow-ups**; panels **Leads per day**, **Pipeline**, **Recent leads**, **Live conversations**, **Recent bookings**, **Live inventory**. Numbers match what you created during UAT.
- [ ] 7.2 Change the website menu and the period (**Last 7/30/90 days**) → the tiles change accordingly.
- [ ] 7.3 Revenue on the dashboard rises by the amount paid in 6.7 (in the period that includes today).
- [ ] 7.4 **Notifications**: the sidebar count matches the unread alerts. **Unread only**, **Mark read** on one, **Mark all read** → count goes to zero. Clicking an alert title opens the right lead / chat / booking.
- [ ] 7.5 You received at least one notification of each type during UAT: Lead, Handoff, Payment, Follow-up.
- [ ] 7.6 On **My Account**, untick **Email notifications**, **Save** → *"Your notification settings were saved."*; trigger an alert (e.g. assign yourself a lead from another account) → it appears in-app but no email arrives. Tick it again.
- [ ] 7.7 **Reports** (Manager): **Website**, **Period**, **From**, **To**, **Apply** → the line *"Showing … – …"* reflects the choice; a From/To range says *(custom range; it overrides the period)*.
- [ ] 7.8 Reports show **Leads by source**, **Leads by status**, **Bookings by product**, **Top destinations**, **Conversion funnel by website and source**, **By campaign** (your UAT campaign appears), **Revenue by day**.
- [ ] 7.9 **Leads CSV**, **Bookings CSV**, **Revenue CSV**, **Conversion CSV** each download a CSV that opens in Excel/Sheets with the columns described in the user guide, honouring the website/date filters.
- [ ] 7.10 **Analytics**: **Website performance** has a row per website (chats, chat→lead %, leads, converted, rate, bookings, paid, revenue); **Employee performance** shows Agent A with the handoff from 5.4 under **Handoffs taken** and an **Avg first response**.
- [ ] 7.11 As Employee, **Reports**/**Analytics** are not in the sidebar and CSV links are not shown; typing `/dashboard/reports/` gives the permission message.
- [ ] 7.12 Leave the inbox open as Manager while a customer requests a human → the sidebar **Conversations** badge updates without reloading.
- [ ] 7.13 As Employee (Agent A), the **Dashboard** shows a **My figures** badge and **My revenue**; the numbers only count Agent A's own/visible leads, chats and bookings (compare with the Manager's dashboard, which is higher if other agents have records).
- [ ] 7.14 As Inventory User, the **Dashboard** shows the inventory overview (hotels, cars, packages, destinations with inactive/archived counts, **Hidden items**, **Room offers valid today**, **Recently updated**) and no leads, bookings or revenue.
- [ ] 7.15 Signed in as Employee, open `/api/v1/dashboard/overview/` → `"scope": "mine"`; `/api/v1/dashboard/overview/?scope=business` → an error (403). As Inventory User the first gives `"scope": "inventory"` and `?scope=mine` an error.

## P8 — AI validation, payments, security and browsers

### AI and Knowledge Base

- [ ] 8.1 As Admin, **AI Settings** → choose **Provider**, **Model**, paste a key, **Save settings** → *"AI settings saved."*; the key field shows only a masked hint (*"Saved: sk-ant…1234. Leave blank to keep it."*).
- [ ] 8.2 **Test connection** → *"Connected — … is available."*. Paste an invalid key, save, test → a clear rejection message. Restore the valid key.
- [ ] 8.3 The **Audit log** shows `ai_settings.update` and does not contain the key.
- [ ] 8.4 As Manager, **Knowledge Base → Add article**: category **Cancellation**, **All websites**, keywords `cancel, cancellation`, content with an exact fee and deadline → **Save article**.
- [ ] 8.5 In the widget ask *"What is your cancellation policy?"* → the answer matches the article's facts and adds nothing invented.
- [ ] 8.6 Ask about something not in the Knowledge Base (e.g. *"Do you arrange visas for Mongolia?"*) → with an AI model, it says it does not have that information and offers a consultant.
- [ ] 8.7 Untick **Is active** on the article → the next chat no longer quotes it.
- [ ] 8.8 Switch **Let AI write chat replies** off → chats still answer (rule-based) and still create leads. Switch back on.

### Payments (live test keys required for 8.9–8.12)

- [ ] 8.9 In the Razorpay dashboard (test mode) the webhook to `https://<staging>/api/v1/bookings/payments/webhook/` is enabled for `payment.captured`, `order.paid`, `payment.failed`, `refund.processed` with the same secret as `RAZORPAY_WEBHOOK_SECRET`.
- [ ] 8.10 Pay a booking with a test card and close the browser tab immediately after paying → within a minute the booking is **Confirmed** anyway (webhook).
- [ ] 8.11 Pay with a test card that fails → the booking shows **Failed**, the pay page says *"The last payment attempt did not go through"*, and paying again with a good card confirms it.
- [ ] 8.12 Refund a confirmed test payment **in full** in the Razorpay dashboard → the booking becomes **Refunded**, the lead **Lost**, the customer gets *"Refund processed for booking …"*, managers are notified. A **partial** refund of another booking → it stays **Confirmed**, the customer gets a refund notice for that amount.

### Security and permissions

- [ ] 8.13 As Agent B, open URLs of Agent A's lead, chat, booking and follow-up edit page → each is page not found.
- [ ] 8.14 As Employee, the **Customer** and **Lead** menus on **Create booking**, the **Customer** menu on **Add lead** and the **Lead** menu on a new follow-up only list records you can see.
- [ ] 8.15 Embed the widget snippet on a page served from a domain that is **not** the website's registered domain → the widget does not work there.
- [ ] 8.16 After revoking a key (2.3), a page still using it gets no answers.
- [ ] 8.17 Send messages very quickly in the widget (over 30 a minute) → *"You are sending messages very quickly — please wait a moment."*
- [ ] 8.18 Sign-in and all dashboard pages are served over HTTPS with a valid certificate (padlock, no warning).
- [ ] 8.19 **Audit log** (Admin) lists the UAT actions with actor and IP — user changes, key issue/revoke, lead status changes, takeovers, bookings, payments, cancellations. (Sign-ins themselves are not logged.)

### Cross-browser

- [ ] 8.20 Repeat 1.3, 3.7/3.8, 4.1–4.3, 5.4–5.5 and 6.6–6.7 in each browser you support. Tick each that passed:
  - [ ] Chrome (desktop) - [ ] Firefox (desktop) - [ ] Safari (desktop) - [ ] Edge (desktop)
  - [ ] Chrome (Android) - [ ] Safari (iPhone)
- [ ] 8.21 The automated cross-browser smoke suite (`browser_tests/run.sh`, Chromium/Firefox/WebKit × desktop/mobile) passed on this build. Run date / result: ______________

## P9 — Deployment and handover

- [ ] 9.1 `./docker/verify.sh` on the server ends with *"Everything checks out."* (or only warnings you accept). Output attached.
- [ ] 9.2 Every item in DEPLOY.md *Go-live checklist* is done for production.
- [ ] 9.3 A nightly backup exists: `docker compose exec web ls -lh /app/data/backups` shows today's or yesterday's `db-YYYY-MM-DD.sqlite3`.
- [ ] 9.4 A restore has been rehearsed on staging following DEPLOY.md *Restore a backup*.
- [ ] 9.5 The demo accounts (`admin` / `travel1234` etc.) do not exist on production, and no demo data was loaded there.
- [ ] 9.6 The client has received USER_GUIDE.md, this checklist and FIP_TRACEABILITY.md, and admin credentials have been handed over securely.

---

## Issues found

| # | Item | What happened | Severity (blocker / major / minor) | Fixed in build | Retested by |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

## Sign-off

By signing, the client confirms that the items above were tested on staging
and the build is accepted for production, subject to any issues listed as
open.

| Role | Name | Signature | Date | Accepted (Yes / With conditions / No) |
|---|---|---|---|---|
| Client — business owner | | | | |
| Client — operations / sales lead | | | | |
| Client — finance (payments) | | | | |
| Supplier — project lead | | | | |
| Supplier — QA | | | | |

Conditions / open items accepted for go-live:

______________________________________________________________________

______________________________________________________________________
