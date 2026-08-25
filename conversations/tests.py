import json
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from crm.models import Lead, LeadSource
from inventory.models import Destination, Hotel, InventoryType
from websites.models import Website
from websites.services import issue_api_key

from . import ai, services
from .models import Conversation, ConversationStatus, Message, MessageSender


class ExtractionTests(TestCase):
    def setUp(self):
        Destination.objects.create(name="Goa", code="goa", city="Panaji")
        Destination.objects.create(name="Manali", code="manali", city="Manali")

    def test_destination_is_matched_against_real_destinations(self):
        requirements = ai.extract_requirements("I want to visit goa in winter")
        self.assertEqual(requirements["destination"], "Goa")

    def test_an_unknown_place_is_not_invented(self):
        requirements = ai.extract_requirements("I want to visit Atlantis")
        self.assertNotIn("destination", requirements)

    def test_product_type_from_keywords(self):
        self.assertEqual(ai.extract_requirements("need a hotel")["product_type"], "hotel")
        self.assertEqual(ai.extract_requirements("need a cab")["product_type"], "car")
        self.assertEqual(ai.extract_requirements("honeymoon package")["product_type"], "package")

    def test_traveller_count(self):
        self.assertEqual(ai.extract_requirements("for 4 people")["travelers"], 4)
        self.assertEqual(ai.extract_requirements("6 adults going")["travelers"], 6)

    def test_budget_units(self):
        self.assertEqual(ai.extract_requirements("budget 40k")["budget_max"], 40000)
        self.assertEqual(ai.extract_requirements("around 1.5 lakh")["budget_max"], 150000)
        self.assertEqual(ai.extract_requirements("₹25,000 please")["budget_max"], 25000)

    def test_small_numbers_are_not_read_as_a_budget(self):
        self.assertNotIn("budget_max", ai.extract_requirements("we are 4 people"))

    def test_a_date_is_not_mistaken_for_a_budget(self):
        # Regression: "12/03/2027" used to parse as a 2027 budget, which then
        # filtered every real option out of the recommendations.
        for text in (
            "We are travelling 12/03/2027, can you share options?",
            "travelling on 2027-03-12",
            "12 March 2027 departure",
        ):
            self.assertNotIn("budget_max", ai.extract_requirements(text), text)

    def test_a_bare_year_is_not_a_budget_but_a_prefixed_amount_is(self):
        self.assertNotIn("budget_max", ai.extract_requirements("maybe 2027 works"))
        self.assertEqual(ai.extract_requirements("rs 2000 budget")["budget_max"], 2000)

    def test_a_date_and_a_budget_in_one_message_are_both_read(self):
        requirements = ai.extract_requirements("Goa on 12/03/2027 with budget 40k")
        self.assertEqual(requirements["travel_start"], "2027-03-12")
        self.assertEqual(requirements["budget_max"], 40000)

    def test_iso_and_slash_dates(self):
        self.assertEqual(
            ai.extract_requirements("travelling 2027-03-12")["travel_start"], "2027-03-12"
        )
        self.assertEqual(
            ai.extract_requirements("travelling 12/03/2027")["travel_start"], "2027-03-12"
        )

    def test_a_date_range_fills_both_ends(self):
        requirements = ai.extract_requirements("from 2027-03-12 to 2027-03-18")
        self.assertEqual(requirements["travel_start"], "2027-03-12")
        self.assertEqual(requirements["travel_end"], "2027-03-18")

    def test_requirements_accumulate_across_turns(self):
        first = ai.extract_requirements("hotel in Goa")
        second = ai.extract_requirements("for 4 people", first)
        self.assertEqual(second["destination"], "Goa")
        self.assertEqual(second["travelers"], 4)

    def test_email_and_phone_capture(self):
        contact = ai.extract_contact("reach me at ana@example.com or 9810012345")
        self.assertEqual(contact["email"], "ana@example.com")
        self.assertEqual(contact["phone"], "9810012345")

    def test_digits_inside_an_email_are_not_read_as_a_phone_number(self):
        contact = ai.extract_contact("mail me at user9876543210@example.com")
        self.assertEqual(contact["email"], "user9876543210@example.com")
        self.assertNotIn("phone", contact)

    def test_handoff_phrases(self):
        wanted, reason = ai.detect_handoff("can I talk to a human please")
        self.assertTrue(wanted)
        self.assertIn("human", reason)
        self.assertFalse(ai.detect_handoff("what hotels do you have")[0])


class RuleReplyTests(TestCase):
    def setUp(self):
        self.goa = Destination.objects.create(name="Goa", code="goa")
        self.hotel = Hotel.objects.create(
            name="Palm Stay", destination=self.goa, base_price=Decimal("3000")
        )
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main", brand_name="Scared"
        )

    def test_a_greeting_gets_a_welcome(self):
        reply = ai.compose_rule_reply("hi", {}, [], "Scared")
        self.assertIn("Scared", reply)

    def test_matches_are_listed_with_their_real_price(self):
        conversation = Conversation.objects.create(session_key="s1", website=self.website)
        result = ai.generate_reply(
            conversation=conversation, message="hotel in Goa for 2 people"
        )
        self.assertIn("Palm Stay", result.reply)
        self.assertIn("3,000", result.reply)
        self.assertEqual(result.engine, "rules")

    def test_a_greeting_gets_no_inventory_cards(self):
        # Regression: "Hi" used to come back with three arbitrary items attached.
        conversation = Conversation.objects.create(session_key="s2", website=self.website)
        result = ai.generate_reply(conversation=conversation, message="Hi")
        self.assertEqual(result.recommendations, [])

    def test_a_product_type_alone_is_enough_to_recommend(self):
        conversation = Conversation.objects.create(session_key="s3", website=self.website)
        result = ai.generate_reply(conversation=conversation, message="show me hotels")
        self.assertTrue(result.recommendations)

    def test_missing_details_are_asked_for(self):
        reply = ai.compose_rule_reply("something", {}, [], "Scared")
        self.assertIn("destination", reply)


class ClaudeLayerTests(TestCase):
    """The Claude path is exercised with a stubbed client — no network calls."""

    def setUp(self):
        self.goa = Destination.objects.create(name="Goa", code="goa")
        self.hotel = Hotel.objects.create(
            name="Palm Stay", destination=self.goa, base_price=Decimal("3000")
        )
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.conversation = Conversation.objects.create(session_key="s1", website=self.website)

    def _stub(self, payload, stop_reason="end_turn"):
        block = mock.Mock(type="text", text=json.dumps(payload))
        response = mock.Mock(content=[block], stop_reason=stop_reason)
        client = mock.Mock()
        client.messages.create.return_value = response
        return client

    def test_claude_reply_and_requirements_are_used(self):
        payload = {
            "reply": "I found a great stay in Goa.",
            "requirements": {
                "destination": "Goa",
                "product_type": "hotel",
                "travel_start": "2027-03-12",
                "travel_end": "",
                "travelers": 2,
                "budget_max": 0,
            },
            "recommended_ids": [f"hotel:{self.hotel.pk}"],
            "should_handoff": False,
            "handoff_reason": "",
        }
        with mock.patch.object(ai, "_client", return_value=self._stub(payload)):
            result = ai.generate_reply(conversation=self.conversation, message="hotel in Goa")

        self.assertEqual(result.engine, "claude")
        self.assertEqual(result.reply, "I found a great stay in Goa.")
        self.assertEqual(result.requirements["travel_start"], "2027-03-12")
        self.assertEqual(result.recommendations[0]["name"], "Palm Stay")

    def test_empty_claude_values_do_not_erase_what_the_rules_found(self):
        payload = {
            "reply": "Tell me more.",
            "requirements": {
                "destination": "",
                "product_type": "",
                "travel_start": "",
                "travel_end": "",
                "travelers": 0,
                "budget_max": 0,
            },
            "recommended_ids": [],
            "should_handoff": False,
            "handoff_reason": "",
        }
        with mock.patch.object(ai, "_client", return_value=self._stub(payload)):
            result = ai.generate_reply(
                conversation=self.conversation, message="hotel in Goa for 4 people"
            )
        self.assertEqual(result.requirements["destination"], "Goa")
        self.assertEqual(result.requirements["travelers"], 4)

    def test_invented_ids_are_ignored(self):
        payload = {
            "reply": "Here you go.",
            "requirements": {
                "destination": "Goa", "product_type": "hotel", "travel_start": "",
                "travel_end": "", "travelers": 0, "budget_max": 0,
            },
            "recommended_ids": ["hotel:999999"],
            "should_handoff": False,
            "handoff_reason": "",
        }
        with mock.patch.object(ai, "_client", return_value=self._stub(payload)):
            result = ai.generate_reply(conversation=self.conversation, message="hotel in Goa")
        # Falls back to real matches instead of a fabricated item.
        for item in result.recommendations:
            self.assertTrue(Hotel.objects.filter(pk=item["id"]).exists())

    def test_a_refusal_hands_off_to_a_human(self):
        client = self._stub({}, stop_reason="refusal")
        with mock.patch.object(ai, "_client", return_value=client):
            result = ai.generate_reply(conversation=self.conversation, message="something")
        self.assertTrue(result.should_handoff)

    def test_an_api_failure_falls_back_to_the_rule_engine(self):
        client = mock.Mock()
        client.messages.create.side_effect = RuntimeError("network down")
        with mock.patch.object(ai, "_client", return_value=client):
            result = ai.generate_reply(conversation=self.conversation, message="hotel in Goa")
        self.assertEqual(result.engine, "rules")
        self.assertTrue(result.reply)

    def test_malformed_json_falls_back_to_the_rule_engine(self):
        block = mock.Mock(type="text", text="not json at all")
        client = mock.Mock()
        client.messages.create.return_value = mock.Mock(content=[block], stop_reason="end_turn")
        with mock.patch.object(ai, "_client", return_value=client):
            result = ai.generate_reply(conversation=self.conversation, message="hotel in Goa")
        self.assertEqual(result.engine, "rules")

    def test_no_api_key_means_no_client(self):
        with self.settings(ANTHROPIC_API_KEY=""):
            self.assertIsNone(ai._client())


class ConversationFlowTests(TestCase):
    def setUp(self):
        self.goa = Destination.objects.create(name="Goa", code="goa")
        Hotel.objects.create(name="Palm Stay", destination=self.goa, base_price=Decimal("3000"))
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.agent = User.objects.create_user("agent", password="pw", role="employee")

    def test_a_chat_turn_stores_both_messages(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.handle_customer_message(conversation=conversation, text="hotel in Goa")

        senders = list(conversation.messages.values_list("sender_type", flat=True))
        self.assertEqual(senders, [MessageSender.CUSTOMER, MessageSender.AI])

    def test_a_destination_alone_creates_a_lead(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.handle_customer_message(conversation=conversation, text="I want a hotel in Goa")

        conversation.refresh_from_db()
        self.assertIsNotNone(conversation.lead)
        self.assertEqual(conversation.lead.source, LeadSource.AI_CHAT)
        self.assertEqual(conversation.lead.destination, "Goa")

    def test_contact_details_attach_a_customer_to_the_lead(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.handle_customer_message(conversation=conversation, text="hotel in Goa")
        services.handle_customer_message(
            conversation=conversation, text="my email is ana@example.com"
        )

        conversation.refresh_from_db()
        self.assertIsNotNone(conversation.customer)
        self.assertEqual(conversation.customer.email, "ana@example.com")
        self.assertEqual(conversation.lead.customer, conversation.customer)

    def test_one_lead_per_conversation_however_many_turns(self):
        conversation, _ = services.start_conversation(website=self.website)
        for text in ("hotel in Goa", "for 4 people", "budget 40k"):
            services.handle_customer_message(conversation=conversation, text=text)
        self.assertEqual(Lead.objects.count(), 1)

    def test_later_turns_enrich_the_existing_lead(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.handle_customer_message(conversation=conversation, text="hotel in Goa")
        services.handle_customer_message(
            conversation=conversation, text="for 4 people, budget 40k, on 2027-03-12"
        )
        lead = Lead.objects.get()
        self.assertEqual(lead.travelers_count, 4)
        self.assertEqual(lead.budget_max, 40000)
        self.assertEqual(lead.travel_start, date(2027, 3, 12))

    def test_small_talk_alone_creates_no_lead(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.handle_customer_message(conversation=conversation, text="hi there")
        self.assertEqual(Lead.objects.count(), 0)

    def test_asking_for_a_human_moves_the_chat_to_waiting(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.handle_customer_message(
            conversation=conversation, text="I want to talk to a human about Goa"
        )
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, ConversationStatus.WAITING)

    def test_take_over_stops_the_ai_from_replying(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.take_over(conversation=conversation, user=self.agent)
        before = conversation.messages.count()

        services.handle_customer_message(conversation=conversation, text="hotel in Goa")

        # Only the customer's own message is added — no AI reply.
        self.assertEqual(conversation.messages.count(), before + 1)
        self.assertEqual(
            conversation.messages.last().sender_type, MessageSender.CUSTOMER
        )

    def test_resume_ai_puts_the_bot_back_in_charge(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.take_over(conversation=conversation, user=self.agent)
        services.resume_ai(conversation=conversation, user=self.agent)

        conversation.refresh_from_db()
        self.assertEqual(conversation.status, ConversationStatus.AI_ACTIVE)
        handoff = conversation.handoffs.first()
        self.assertIsNotNone(handoff.resumed_ai_at)

    def test_history_starts_with_a_customer_turn_and_merges_runs(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.post_message(
            conversation=conversation, sender_type=MessageSender.AI, content="Welcome"
        )
        services.post_message(
            conversation=conversation, sender_type=MessageSender.CUSTOMER, content="one"
        )
        services.post_message(
            conversation=conversation, sender_type=MessageSender.CUSTOMER, content="two"
        )
        history = services.build_history(conversation)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "one\ntwo")

    def test_an_agent_reply_takes_the_chat_over_automatically(self):
        conversation, _ = services.start_conversation(website=self.website)
        services.agent_reply(conversation=conversation, user=self.agent, text="Hello!")
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, ConversationStatus.HUMAN_ACTIVE)
        self.assertEqual(conversation.assigned_to, self.agent)


class WidgetAPITests(TestCase):
    def setUp(self):
        self.goa = Destination.objects.create(name="Goa", code="goa")
        Hotel.objects.create(name="Palm Stay", destination=self.goa, base_price=Decimal("3000"))
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.key = issue_api_key(website=self.website)
        self.url = reverse("api_conversations:widget_chat")

    def post(self, payload):
        return self.client.post(self.url, payload, content_type="application/json")

    def test_a_valid_key_starts_a_conversation_and_answers(self):
        response = self.post({"key": self.key.public_key, "message": "hotel in Goa"})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["session"])
        self.assertTrue(data["is_new_session"])
        self.assertIn("Palm Stay", data["reply"]["content"])

    def test_only_this_turns_recommendations_are_returned(self):
        # Regression: the widget used to show cards left over from earlier turns,
        # so a Kerala question came back with Goa and Jaipur options attached.
        Destination.objects.create(name="Manali", code="manali")
        Hotel.objects.create(
            name="Snow Lodge",
            destination=Destination.objects.get(code="manali"),
            base_price=Decimal("5000"),
        )
        first = self.post({"key": self.key.public_key, "message": "hotel in Goa"}).json()["data"]
        second = self.post(
            {
                "key": self.key.public_key,
                "session": first["session"],
                "message": "actually a hotel in Manali",
            }
        ).json()["data"]

        titles = [rec["title"] for rec in second["recommendations"]]
        self.assertIn("Snow Lodge", titles)
        self.assertNotIn("Palm Stay", titles)

    def test_an_unknown_key_is_rejected(self):
        response = self.post({"key": "pk_nope", "message": "hi"})
        self.assertEqual(response.status_code, 403)

    def test_a_revoked_key_is_rejected(self):
        self.key.is_active = False
        self.key.save()
        response = self.post({"key": self.key.public_key, "message": "hi"})
        self.assertEqual(response.status_code, 403)

    def test_a_disabled_widget_is_rejected(self):
        self.website.widget_enabled = False
        self.website.save()
        response = self.post({"key": self.key.public_key, "message": "hi"})
        self.assertEqual(response.status_code, 403)

    def test_the_same_session_key_continues_the_same_conversation(self):
        first = self.post({"key": self.key.public_key, "message": "hi"}).json()["data"]
        second = self.post(
            {"key": self.key.public_key, "message": "hotel in Goa", "session": first["session"]}
        ).json()["data"]

        self.assertFalse(second["is_new_session"])
        self.assertEqual(Conversation.objects.count(), 1)

    def test_a_session_from_another_website_is_refused(self):
        other = Website.objects.create(
            name="Other", domain="other.com", source_identifier="other"
        )
        other_key = issue_api_key(website=other)
        session = self.post({"key": self.key.public_key, "message": "hi"}).json()["data"]["session"]

        response = self.post(
            {"key": other_key.public_key, "message": "hi", "session": session}
        )
        self.assertEqual(response.status_code, 403)

    def test_an_empty_message_is_rejected(self):
        response = self.post({"key": self.key.public_key, "message": ""})
        self.assertEqual(response.status_code, 400)

    def test_polling_returns_agent_replies_only_after_the_given_id(self):
        data = self.post({"key": self.key.public_key, "message": "hi"}).json()["data"]
        conversation = Conversation.objects.get(session_key=data["session"])
        agent = User.objects.create_user("agent", password="pw", role="employee")
        services.agent_reply(conversation=conversation, user=agent, text="A human here.")

        response = self.client.get(
            self.url,
            {"key": self.key.public_key, "session": data["session"], "since": data["reply"]["id"]},
        )
        contents = [m["content"] for m in response.json()["data"]["messages"]]
        self.assertIn("A human here.", contents)
        self.assertNotIn(data["reply"]["content"], contents)

    def test_the_widget_endpoint_needs_no_login(self):
        # Anonymous by design — the public key is the credential.
        self.assertEqual(
            self.post({"key": self.key.public_key, "message": "hi"}).status_code, 200
        )


class InboxViewTests(TestCase):
    def setUp(self):
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        self.client.force_login(self.agent)

    def test_the_inbox_hides_closed_chats(self):
        live, _ = services.start_conversation(website=self.website)
        closed, _ = services.start_conversation(website=self.website)
        services.close_conversation(conversation=closed, user=self.agent)

        response = self.client.get(reverse("conversations:inbox"))
        self.assertContains(response, live.session_key[:10])
        self.assertNotContains(response, closed.session_key[:10])

    def test_history_shows_closed_chats(self):
        closed, _ = services.start_conversation(website=self.website)
        services.close_conversation(conversation=closed, user=self.agent)
        response = self.client.get(reverse("conversations:history"))
        self.assertContains(response, closed.session_key[:10])

    def test_replying_from_the_workspace_posts_an_agent_message(self):
        conversation, _ = services.start_conversation(website=self.website)
        self.client.post(
            reverse("conversations:reply", args=[conversation.pk]),
            {"content": "Hello from a human"},
        )
        self.assertTrue(
            Message.objects.filter(
                conversation=conversation, sender_type=MessageSender.AGENT
            ).exists()
        )
