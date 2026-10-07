import json
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from crm.models import Lead, LeadSource
from inventory.models import Destination, Hotel, InventoryType
from websites.models import Website
from websites.services import issue_api_key

from . import ai, services
from .models import AISettings, Conversation, ConversationStatus, Message, MessageSender


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
        # Current models go through the beta endpoint (refusal fallbacks); older ones don't.
        client.messages.create.return_value = response
        client.beta.messages.create.return_value = response
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
        client.beta.messages.create.side_effect = RuntimeError("network down")
        with mock.patch.object(ai, "_client", return_value=client):
            result = ai.generate_reply(conversation=self.conversation, message="hotel in Goa")
        self.assertEqual(result.engine, "rules")
        self.assertTrue(result.reply)

    def test_malformed_json_falls_back_to_the_rule_engine(self):
        block = mock.Mock(type="text", text="not json at all")
        client = mock.Mock()
        client.beta.messages.create.return_value = mock.Mock(content=[block], stop_reason="end_turn")
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

    def test_widget_errors_use_the_error_envelope(self):
        body = self.post({"key": "pk_nope", "message": "hi"}).json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error"]["status_code"], 403)
        # The widget shows this text to the visitor.
        self.assertEqual(body["message"], "Unknown or inactive widget key.")

        response = self.post({"message": "hi"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertIn("key", response.json()["error"]["detail"])

    def test_polling_with_a_bad_since_value_does_not_crash(self):
        session = self.post({"key": self.key.public_key, "message": "hi"}).json()["data"]["session"]
        response = self.client.get(
            self.url, {"key": self.key.public_key, "session": session, "since": "abc"}
        )
        self.assertEqual(response.status_code, 200)

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


GOOD_ANSWER = {
    "reply": "Palm Stay in Goa fits your budget.",
    "requirements": {
        "destination": "Goa",
        "product_type": "hotel",
        "travel_start": "",
        "travel_end": "",
        "travelers": 2,
        "budget_max": 0,
    },
    "recommended_ids": [],
    "should_handoff": False,
    "handoff_reason": "",
}


@override_settings(ANTHROPIC_API_KEY="", GEMINI_API_KEY="", AI_MODEL="claude-opus-5-5")
class AIConfigTests(TestCase):
    def test_nothing_configured_means_rules_only(self):
        self.assertIsNone(ai.resolve_config())

    def test_environment_key_is_the_fallback(self):
        with self.settings(ANTHROPIC_API_KEY="env-key"):
            config = ai.resolve_config()
        self.assertEqual((config.provider, config.model, config.api_key), ("anthropic", "claude-opus-5-5", "env-key"))

    def test_saved_settings_beat_the_environment(self):
        AISettings.objects.create(provider="gemini", model="gemini-3.8-flash", gemini_api_key="saved")
        with self.settings(GEMINI_API_KEY="env-key"):
            config = ai.resolve_config()
        self.assertEqual((config.provider, config.model, config.api_key), ("gemini", "gemini-3.8-flash", "saved"))

    def test_switched_off_means_rules_only(self):
        AISettings.objects.create(enabled=False, anthropic_api_key="saved")
        self.assertIsNone(ai.resolve_config())
        self.assertIsNotNone(ai.resolve_config(respect_enabled=False))

    def test_older_claude_models_skip_thinking_and_fallbacks(self):
        AISettings.objects.create(model="claude-haiku-4-5", anthropic_api_key="k")
        client = mock.Mock()
        block = mock.Mock(type="text", text=json.dumps(GOOD_ANSWER))
        client.messages.create.return_value = mock.Mock(content=[block], stop_reason="end_turn")
        conversation = Conversation.objects.create(session_key="h1")
        with mock.patch.object(ai, "_client", return_value=client):
            result = ai.generate_reply(conversation=conversation, message="hotel")
        self.assertEqual(result.engine, "claude")
        kwargs = client.messages.create.call_args.kwargs
        self.assertNotIn("thinking", kwargs)
        self.assertNotIn("effort", kwargs["output_config"])
        client.beta.messages.create.assert_not_called()


@override_settings(ANTHROPIC_API_KEY="", GEMINI_API_KEY="")
class GeminiLayerTests(TestCase):
    """Gemini is exercised with a stubbed HTTP call — no network."""

    def setUp(self):
        self.goa = Destination.objects.create(name="Goa", code="goa")
        self.hotel = Hotel.objects.create(name="Palm Stay", destination=self.goa, base_price=Decimal("3000"))
        AISettings.objects.create(provider="gemini", model="gemini-3.8-flash", gemini_api_key="g-key")
        self.conversation = Conversation.objects.create(session_key="g1")

    def _reply(self, text, finish="STOP"):
        return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}

    def test_gemini_answer_is_used(self):
        answer = dict(GOOD_ANSWER, recommended_ids=[f"hotel:{self.hotel.pk}"])
        with mock.patch.object(ai, "_gemini_request", return_value=self._reply(json.dumps(answer))) as call:
            result = ai.generate_reply(
                conversation=self.conversation,
                message="hotel in Goa",
                history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello"}],
            )
        self.assertEqual(result.engine, "gemini")
        self.assertEqual(result.reply, GOOD_ANSWER["reply"])
        self.assertEqual(result.recommendations[0]["name"], "Palm Stay")
        url, key, body = call.call_args.args
        self.assertTrue(url.endswith("/models/gemini-3.8-flash:generateContent"))
        self.assertEqual(key, "g-key")
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model", "user"])
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")

    def test_a_safety_block_hands_off_to_a_human(self):
        with mock.patch.object(ai, "_gemini_request", return_value=self._reply("", finish="SAFETY")):
            result = ai.generate_reply(conversation=self.conversation, message="hotel in Goa")
        self.assertTrue(result.should_handoff)

    def test_a_network_failure_falls_back_to_rules(self):
        import urllib.error

        with mock.patch.object(ai, "_gemini_request", side_effect=urllib.error.URLError("down")):
            result = ai.generate_reply(conversation=self.conversation, message="hotel in Goa")
        self.assertEqual(result.engine, "rules")

    def test_check_connection_reports_an_unknown_model(self):
        import urllib.error

        error = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        config = ai.resolve_config()
        with mock.patch.object(ai, "_gemini_request", side_effect=error):
            ok, message = ai.check_connection(config)
        self.assertFalse(ok)
        self.assertIn("gemini-3.8-flash", message)


@override_settings(ANTHROPIC_API_KEY="", GEMINI_API_KEY="")
class AISettingsPageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("boss", password="pw", role="admin")
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        self.url = reverse("conversations:ai_settings")

    def _post(self, **overrides):
        data = {"enabled": "on", "provider": "anthropic", "model": "claude-opus-5-5", "custom_model": ""}
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_only_admins_can_open_it(self):
        self.client.force_login(self.agent)
        self.assertRedirects(self.client.get(self.url), reverse("dashboard:overview"))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_saving_a_key_never_echoes_it_back(self):
        self.client.force_login(self.admin)
        self._post(anthropic_api_key="sk-ant-secret-value-1234")
        stored = AISettings.load()
        self.assertEqual(stored.anthropic_api_key, "sk-ant-secret-value-1234")
        self.assertEqual(stored.updated_by, self.admin)
        page = self.client.get(self.url).content.decode()
        self.assertNotIn("sk-ant-secret-value-1234", page)
        self.assertIn("1234", page)  # the masked hint

    def test_a_blank_key_keeps_the_saved_one_and_clear_removes_it(self):
        AISettings.objects.create(anthropic_api_key="keep-me-please-0001")
        self.client.force_login(self.admin)
        self._post(model="claude-sonnet-5-5")
        self.assertEqual(AISettings.load().anthropic_api_key, "keep-me-please-0001")
        self.assertEqual(AISettings.load().model, "claude-sonnet-5-5")
        self._post(clear_anthropic_key="on")
        self.assertEqual(AISettings.load().anthropic_api_key, "")

    def test_model_must_match_the_provider_unless_typed_in(self):
        self.client.force_login(self.admin)
        response = self._post(provider="gemini", model="claude-opus-5-5")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AISettings.objects.exists())
        self._post(provider="gemini", model="claude-opus-5-5", custom_model="gemini-4-flash")
        self.assertEqual(AISettings.load().model, "gemini-4-flash")

    def test_the_audit_log_records_the_change_but_not_the_key(self):
        from accounts.models import AuditLog

        self.client.force_login(self.admin)
        self._post(gemini_api_key="AIza-secret", provider="gemini", model="gemini-3.8-flash")
        entry = AuditLog.objects.get(action="ai_settings.update")
        self.assertEqual(entry.metadata["keys_changed"], ["gemini_api_key"])
        self.assertNotIn("AIza-secret", json.dumps(entry.metadata))


class ConversationAccessTests(TestCase):
    def setUp(self):
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        self.other = User.objects.create_user("other", password="pw", role="employee")
        self.manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.theirs, _ = services.start_conversation(website=self.website)
        services.take_over(conversation=self.theirs, user=self.other)
        self.client.force_login(self.agent)

    def test_every_chat_page_and_action_404s_on_another_employees_chat(self):
        pk = self.theirs.pk
        self.assertEqual(
            self.client.get(reverse("conversations:detail", args=[pk])).status_code, 404
        )
        for name in ("conversations:take_over", "conversations:resume_ai", "conversations:close"):
            self.assertEqual(self.client.post(reverse(name, args=[pk])).status_code, 404, name)
        response = self.client.post(
            reverse("conversations:reply", args=[pk]), {"content": "Hijack"}
        )
        self.assertEqual(response.status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.assigned_to, self.other)
        self.assertEqual(self.theirs.status, ConversationStatus.HUMAN_ACTIVE)

    def test_the_apis_are_scoped_too(self):
        pk = self.theirs.pk
        self.assertEqual(
            self.client.get(reverse("api_conversations:detail", args=[pk])).status_code, 404
        )
        response = self.client.post(
            reverse("api_conversations:handoff", args=[pk]),
            {"action": "take_over"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.assigned_to, self.other)

    def test_an_unassigned_waiting_chat_can_be_opened_and_taken_over(self):
        waiting, _ = services.start_conversation(website=self.website)
        Conversation.objects.filter(pk=waiting.pk).update(status=ConversationStatus.WAITING)
        self.assertEqual(
            self.client.get(reverse("conversations:detail", args=[waiting.pk])).status_code, 200
        )
        self.client.post(reverse("conversations:take_over", args=[waiting.pk]))
        waiting.refresh_from_db()
        self.assertEqual(waiting.assigned_to, self.agent)

    def test_managers_can_open_any_chat(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("conversations:detail", args=[self.theirs.pk]))
        self.assertEqual(response.status_code, 200)

    def test_the_inventory_role_cannot_reach_conversations(self):
        stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.client.force_login(stock)
        self.assertRedirects(
            self.client.get(reverse("conversations:inbox")), reverse("dashboard:overview")
        )
        self.assertEqual(self.client.get(reverse("api_conversations:inbox")).status_code, 403)



# --------------------------------------------------------------------------
# Batch 2: booking from the chat, history, handoff behaviour, auto-resume
# --------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from django.core.cache import cache  # noqa: E402
from django.utils import timezone  # noqa: E402

from accounts.models import AuditLog  # noqa: E402
from bookings.models import Booking, BookingStatus, Notification  # noqa: E402
from crm.models import LeadActivity, LeadStatus  # noqa: E402
from inventory.models import CarRental  # noqa: E402

from .models import Recommendation  # noqa: E402


class PublicWidgetFixture(TestCase):
    def setUp(self):
        # Throttle counters live in the cache; start every test with a clean slate.
        cache.clear()
        self.goa = Destination.objects.create(name="Goa", code="goa")
        self.hotel = Hotel.objects.create(
            name="Palm Stay", destination=self.goa, base_price=Decimal("3000"), star_rating=3
        )
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.key = issue_api_key(website=self.website)
        self.manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.chat_url = reverse("api_conversations:widget_chat")
        self.history_url = reverse("api_conversations:widget_history")
        self.book_url = reverse("api_conversations:widget_book")

    def chat(self, message, session="", **headers):
        return self.client.post(
            self.chat_url,
            {"key": self.key.public_key, "message": message, "session": session},
            content_type="application/json",
            **headers,
        )

    def start_with_cards(self):
        data = self.chat("hotel in Goa").json()["data"]
        return data["session"], data["reply"]["cards"]

    def book(self, session, recommendation_id, **overrides):
        start = timezone.localdate() + timedelta(days=10)
        payload = {
            "key": self.key.public_key,
            "session": session,
            "recommendation_id": recommendation_id,
            "name": "Ana Rao",
            "email": "ana@example.com",
            "phone": "9810000000",
            "travel_start": start.isoformat(),
            "travel_end": (start + timedelta(days=3)).isoformat(),
            "travelers": 2,
        }
        payload.update(overrides)
        return self.client.post(self.book_url, payload, content_type="application/json")


class WidgetBookTests(PublicWidgetFixture):
    def test_cards_carry_a_bookable_recommendation_id(self):
        _session, cards = self.start_with_cards()
        self.assertEqual(cards[0]["title"], "Palm Stay")
        self.assertTrue(cards[0]["bookable"])
        self.assertTrue(Recommendation.objects.filter(pk=cards[0]["recommendation_id"]).exists())

    def test_booking_from_the_widget_creates_a_pending_booking_and_pay_link(self):
        session, cards = self.start_with_cards()
        response = self.book(session, cards[0]["recommendation_id"])

        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        booking = Booking.objects.get()
        self.assertEqual(booking.status, BookingStatus.PENDING)
        # Server-side price: 3 nights × ₹3,000, plus 5% tax.
        self.assertEqual(booking.subtotal, Decimal("9000.00"))
        self.assertEqual(booking.tax_amount, Decimal("450.00"))
        self.assertEqual(booking.total_amount, Decimal("9450.00"))
        self.assertEqual(Decimal(str(data["booking"]["total"])), Decimal("9450"))
        self.assertIn(f"/pay/{booking.payment_token}/", data["payment_url"])
        self.assertTrue(data["payment_url"].startswith("http"))

        conversation = Conversation.objects.get(session_key=session)
        self.assertEqual(booking.conversation, conversation)
        self.assertEqual(booking.customer.email, "ana@example.com")
        self.assertEqual(booking.customer.first_name, "Ana")
        self.assertEqual(booking.lead, conversation.lead)
        self.assertTrue(Recommendation.objects.get(pk=cards[0]["recommendation_id"]).is_selected)
        note = conversation.messages.filter(sender_type=MessageSender.SYSTEM).last()
        self.assertEqual(note.metadata["event"], "booking_created")
        self.assertIn(booking.booking_number, note.content)
        self.assertTrue(
            Notification.objects.filter(recipient=self.manager, metadata__booking_id=booking.pk)
        )

        lead = conversation.lead
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.PAYMENT_PENDING)
        moves = list(
            LeadActivity.objects.filter(lead=lead, activity_type="status_change")
            .order_by("created_at")
            .values_list("details__to", flat=True)
        )
        self.assertEqual(moves[-2:], [LeadStatus.INTERESTED, LeadStatus.PAYMENT_PENDING])

    def test_prices_sent_by_the_browser_are_ignored(self):
        session, cards = self.start_with_cards()
        self.book(session, cards[0]["recommendation_id"], subtotal="1", price="1", total="1")
        self.assertEqual(Booking.objects.get().subtotal, Decimal("9000.00"))

    def test_a_recommendation_from_another_chat_is_rejected(self):
        session, _cards = self.start_with_cards()
        _other_session, other_cards = self.start_with_cards()
        response = self.book(session, other_cards[0]["recommendation_id"])
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Booking.objects.exists())

    def test_another_websites_key_cannot_book_into_this_chat(self):
        session, cards = self.start_with_cards()
        other = Website.objects.create(name="Other", domain="other.com", source_identifier="other")
        other_key = issue_api_key(website=other)
        response = self.book(session, cards[0]["recommendation_id"], key=other_key.public_key)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Booking.objects.exists())

    def test_missing_contact_details_are_reported_per_field(self):
        session, cards = self.start_with_cards()
        response = self.book(session, cards[0]["recommendation_id"], email="", phone="")
        self.assertEqual(response.status_code, 400)
        detail = response.json()["error"]["detail"]
        self.assertIn("email", detail)
        self.assertIn("phone", detail)

    def test_details_already_given_in_the_chat_need_not_be_retyped(self):
        session, cards = self.start_with_cards()
        self.chat("I'm Ana, ana@example.com, 9810000000", session=session)
        response = self.book(session, cards[0]["recommendation_id"], email="", phone="")
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_past_date_is_refused_with_a_readable_message(self):
        session, cards = self.start_with_cards()
        past = (timezone.localdate() - timedelta(days=3)).isoformat()
        response = self.book(session, cards[0]["recommendation_id"], travel_start=past)
        self.assertEqual(response.status_code, 400)
        self.assertIn("past", response.json()["message"])

    def test_asking_twice_returns_the_same_booking(self):
        session, cards = self.start_with_cards()
        self.book(session, cards[0]["recommendation_id"])
        again = self.book(session, cards[0]["recommendation_id"])
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["data"]["created"])
        self.assertEqual(Booking.objects.count(), 1)


class WidgetHistoryTests(PublicWidgetFixture):
    def test_history_restores_the_chat_with_its_cards(self):
        session, cards = self.start_with_cards()
        conversation = Conversation.objects.get(session_key=session)
        services.post_message(
            conversation=conversation,
            sender_type=MessageSender.SYSTEM,
            content="Internal: VIP",
            is_internal=True,
        )
        response = self.client.get(
            self.history_url, {"key": self.key.public_key, "session": session}
        )
        self.assertEqual(response.status_code, 200)
        messages = response.json()["data"]["messages"]
        self.assertEqual([m["sender_type"] for m in messages], ["customer", "ai"])
        self.assertEqual(messages[1]["cards"][0]["recommendation_id"], cards[0]["recommendation_id"])
        self.assertNotIn("Internal: VIP", response.content.decode())

    def test_since_returns_only_newer_messages(self):
        data = self.chat("hi").json()["data"]
        response = self.client.get(
            self.history_url,
            {"key": self.key.public_key, "session": data["session"], "since": data["reply"]["id"]},
        )
        self.assertEqual(response.json()["data"]["messages"], [])

    def test_unknown_or_foreign_sessions_are_404(self):
        session, _cards = self.start_with_cards()
        other = Website.objects.create(name="Other", domain="other.com", source_identifier="other")
        other_key = issue_api_key(website=other)
        for params in (
            {"key": self.key.public_key, "session": "nope"},
            {"key": other_key.public_key, "session": session},
        ):
            self.assertEqual(self.client.get(self.history_url, params).status_code, 404)

    def test_history_lists_the_chats_bookings_with_pay_links(self):
        session, cards = self.start_with_cards()
        self.book(session, cards[0]["recommendation_id"])
        data = self.client.get(
            self.history_url, {"key": self.key.public_key, "session": session}
        ).json()["data"]
        self.assertEqual(len(data["bookings"]), 1)
        self.assertIn("/pay/", data["bookings"][0]["payment_url"])
        created = [m for m in data["messages"] if m["event"] == "booking_created"]
        self.assertIn("/pay/", created[0]["booking"]["payment_url"])


class HumanInvolvedTests(PublicWidgetFixture):
    def setUp(self):
        super().setUp()
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        data = self.chat("hi").json()["data"]
        self.session = data["session"]
        self.conversation = Conversation.objects.get(session_key=self.session)

    def test_no_stale_reply_while_an_agent_owns_the_chat(self):
        services.take_over(conversation=self.conversation, user=self.agent)
        services.agent_reply(conversation=self.conversation, user=self.agent, text="Hello, Ravi here")
        data = self.chat("are you there?", session=self.session).json()["data"]

        # Regression: the last agent/AI message used to come back as a "new" reply.
        self.assertIsNone(data["reply"])
        self.assertEqual(data["messages"], [])
        self.assertEqual(data["status"], ConversationStatus.HUMAN_ACTIVE)
        self.assertTrue(data["awaiting_human"])
        self.assertEqual(data["notice"], "")

    def test_the_ai_stops_answering_while_waiting_for_a_person(self):
        services.request_handoff(conversation=self.conversation, reason="Asked for a human")
        ai_before = self.conversation.messages.filter(sender_type=MessageSender.AI).count()
        data = self.chat("hotel in Goa please", session=self.session).json()["data"]

        self.assertIsNone(data["reply"])
        self.assertEqual(data["status"], ConversationStatus.WAITING)
        self.assertIn("consultant", data["notice"])
        self.assertEqual(
            self.conversation.messages.filter(sender_type=MessageSender.AI).count(), ai_before
        )
        # The message is still stored for the agent, and its details still reach the lead.
        self.assertTrue(
            self.conversation.messages.filter(content="hotel in Goa please").exists()
        )
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.requirements.get("destination"), "Goa")

    def test_a_handoff_records_when_it_was_requested(self):
        services.request_handoff(conversation=self.conversation)
        self.conversation.refresh_from_db()
        self.assertIsNotNone(self.conversation.handoff_requested_at)


class AutoResumeTests(PublicWidgetFixture):
    def setUp(self):
        super().setUp()
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        data = self.chat("hi").json()["data"]
        self.session = data["session"]
        self.conversation = Conversation.objects.get(session_key=self.session)

    def configure(self, wait=0, idle=0):
        config = AISettings.load()
        config.handoff_wait_minutes = wait
        config.agent_idle_minutes = idle
        config.save()

    def wait_since(self, minutes):
        services.request_handoff(conversation=self.conversation)
        Conversation.objects.filter(pk=self.conversation.pk).update(
            handoff_requested_at=timezone.now() - timedelta(minutes=minutes)
        )
        self.conversation.refresh_from_db()

    def test_an_unanswered_handoff_goes_back_to_the_ai_on_the_next_message(self):
        self.configure(wait=15)
        self.wait_since(20)
        data = self.chat("hotel in Goa", session=self.session).json()["data"]

        self.assertEqual(data["status"], ConversationStatus.AI_ACTIVE)
        self.assertIsNotNone(data["reply"])
        engines = [m["content"] for m in data["messages"]]
        self.assertIn(services.AUTO_RESUME_MESSAGES["handoff_wait"], engines)
        self.assertTrue(AuditLog.objects.filter(action="conversation.auto_resume").exists())
        self.assertTrue(
            Notification.objects.filter(recipient=self.manager, metadata__reason="handoff_wait")
        )

    def test_the_timer_is_respected_and_zero_means_never(self):
        self.configure(wait=15)
        self.wait_since(5)
        self.assertFalse(services.maybe_auto_resume(conversation=self.conversation))
        self.configure(wait=0)
        self.wait_since(600)
        self.assertFalse(services.maybe_auto_resume(conversation=self.conversation))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, ConversationStatus.WAITING)

    def test_polling_history_also_triggers_the_resume(self):
        self.configure(wait=15)
        self.wait_since(30)
        data = self.client.get(
            self.history_url, {"key": self.key.public_key, "session": self.session}
        ).json()["data"]
        self.assertEqual(data["status"], ConversationStatus.AI_ACTIVE)

    def test_an_idle_agent_hands_the_chat_back(self):
        self.configure(idle=10)
        services.take_over(conversation=self.conversation, user=self.agent)
        self.conversation.handoffs.update(created_at=timezone.now() - timedelta(minutes=40))
        question = services.post_message(
            conversation=self.conversation, sender_type=MessageSender.CUSTOMER, content="hello?"
        )
        Message.objects.filter(pk=question.pk).update(
            created_at=timezone.now() - timedelta(minutes=15)
        )
        self.assertEqual(services.auto_resume_reason(self.conversation), "agent_idle")
        self.assertTrue(services.maybe_auto_resume(conversation=self.conversation))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, ConversationStatus.AI_ACTIVE)
        self.assertTrue(
            Notification.objects.filter(recipient=self.agent, metadata__reason="agent_idle")
        )

    def test_an_agent_who_replied_is_not_idle(self):
        self.configure(idle=10)
        services.take_over(conversation=self.conversation, user=self.agent)
        question = services.post_message(
            conversation=self.conversation, sender_type=MessageSender.CUSTOMER, content="hello?"
        )
        Message.objects.filter(pk=question.pk).update(
            created_at=timezone.now() - timedelta(minutes=15)
        )
        services.agent_reply(conversation=self.conversation, user=self.agent, text="Here!")
        self.assertEqual(services.auto_resume_reason(self.conversation), "")

    def test_the_sweep_for_a_scheduler_resumes_stale_chats(self):
        self.configure(wait=15)
        self.wait_since(20)
        fresh, _ = services.start_conversation(website=self.website)
        services.request_handoff(conversation=fresh)
        self.assertEqual(services.auto_resume_stale_conversations(), 1)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, ConversationStatus.WAITING)

    def test_ai_settings_page_saves_the_timers(self):
        admin = User.objects.create_user("boss", password="pw", role="admin")
        self.client.force_login(admin)
        self.client.post(
            reverse("conversations:ai_settings"),
            {
                "enabled": "on",
                "provider": "anthropic",
                "model": "claude-opus-5-5",
                "custom_model": "",
                "handoff_wait_minutes": "7",
                "agent_idle_minutes": "0",
            },
        )
        stored = AISettings.load()
        self.assertEqual((stored.handoff_wait_minutes, stored.agent_idle_minutes), (7, 0))
        page = self.client.get(reverse("conversations:ai_settings")).content.decode()
        self.assertIn("handoff_wait_minutes", page)


class PreferenceExtractionTests(TestCase):
    def setUp(self):
        Destination.objects.create(name="Goa", code="goa")

    def test_hotel_food_and_style_wishes_are_picked_up(self):
        requirements = ai.extract_requirements(
            "Honeymoon in Goa, a 5 star hotel with a pool and sea view, pure veg food"
        )
        preferences = requirements["preferences"]
        self.assertEqual(preferences["hotel_stars"], 5)
        self.assertEqual(preferences["amenities"], ["pool", "sea view"])
        self.assertEqual(preferences["food"], "veg")
        self.assertEqual(preferences["trip_style"], "honeymoon")
        # Every key is present so the shape matches the strict model schema.
        self.assertEqual(set(preferences), set(ai.EMPTY_PREFERENCES))

    def test_car_wishes_and_accumulation_across_turns(self):
        first = ai.extract_requirements("need an automatic SUV in Goa")
        second = ai.extract_requirements("with wifi please, non-veg is fine", first)
        preferences = second["preferences"]
        self.assertEqual(preferences["car_type"], "suv")
        self.assertEqual(preferences["transmission"], "automatic")
        self.assertEqual(preferences["food"], "non-veg")
        self.assertEqual(preferences["amenities"], ["wifi"])

    def test_nothing_stated_means_no_preferences_key(self):
        self.assertNotIn("preferences", ai.extract_requirements("hotel in Goa for 2 people"))

    def test_the_strict_schema_requires_every_property(self):
        def check(schema):
            if schema.get("type") == "object":
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(set(schema["required"]), set(schema["properties"]))
                for child in schema["properties"].values():
                    check(child)

        check(ai.RESPONSE_SCHEMA)
        requirements = ai.RESPONSE_SCHEMA["properties"]["requirements"]["properties"]
        self.assertIn("preferences", requirements)
        self.assertIn("budget_min", requirements)

    def test_blank_model_preferences_do_not_erase_rule_findings(self):
        merged = ai.merge_preferences(
            {"hotel_stars": 4, "amenities": ["pool"]}, dict(ai.EMPTY_PREFERENCES)
        )
        self.assertEqual(merged["hotel_stars"], 4)
        self.assertEqual(merged["amenities"], ["pool"])

    def test_a_bare_may_is_not_a_month(self):
        self.assertNotIn("travel_month", ai.extract_requirements("I may go to Goa"))
        self.assertIn("travel_month", ai.extract_requirements("Goa in December"))


class PreferenceMatchingTests(TestCase):
    def setUp(self):
        self.goa = Destination.objects.create(name="Goa", code="goa")
        Hotel.objects.create(name="Budget Inn", destination=self.goa, star_rating=3,
                             base_price=Decimal("2000"))
        Hotel.objects.create(name="Grand Palace", destination=self.goa, star_rating=5,
                             base_price=Decimal("9000"), amenities=["Pool", "Spa"])
        Hotel.objects.create(name="Sea Breeze", destination=self.goa, star_rating=4,
                             base_price=Decimal("6000"), amenities=["Wi-Fi"])
        CarRental.objects.create(name="Swift", destination=self.goa, vehicle_type="Hatchback",
                                 seats=4, daily_price=Decimal("1500"), transmission="Manual")
        CarRental.objects.create(name="Innova", destination=self.goa, vehicle_type="SUV",
                                 seats=7, daily_price=Decimal("3500"), transmission="Automatic")

    def names(self, requirements):
        return [item["name"] for item in ai.match_inventory(requirements)]

    def test_star_rating_is_a_minimum(self):
        names = self.names({
            "destination": "Goa", "product_type": "hotel", "preferences": {"hotel_stars": 4},
        })
        self.assertEqual(sorted(names), ["Grand Palace", "Sea Breeze"])

    def test_wanted_amenities_rank_first(self):
        names = self.names({
            "destination": "Goa", "product_type": "hotel", "preferences": {"amenities": ["wifi"]},
        })
        self.assertEqual(names[0], "Sea Breeze")

    def test_a_car_never_has_fewer_seats_than_travellers(self):
        names = self.names({"destination": "Goa", "product_type": "car", "travelers": 6})
        self.assertEqual(names, ["Innova"])

    def test_car_type_and_transmission_filter(self):
        names = self.names({
            "destination": "Goa",
            "product_type": "car",
            "preferences": {"car_type": "hatchback", "transmission": "manual"},
        })
        self.assertEqual(names, ["Swift"])


class LeadQualificationTests(TestCase):
    def setUp(self):
        Destination.objects.create(name="Goa", code="goa")
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.conversation, _ = services.start_conversation(website=self.website)

    def say(self, text):
        # Each turn is a fresh request: nothing cached from the previous one.
        self.conversation.refresh_from_db()
        services.handle_customer_message(conversation=self.conversation, text=text)
        self.conversation.refresh_from_db()
        return self.conversation.lead

    def test_a_lead_qualifies_once_trip_party_dates_and_contact_are_known(self):
        lead = self.say("hotel in Goa")
        self.assertEqual(lead.status, LeadStatus.NEW)
        lead = self.say("for 2 people from 12 dec to 15 dec")
        self.assertEqual(lead.status, LeadStatus.NEW)  # no way to reach them yet
        lead = self.say("my email is ana@example.com")
        self.assertEqual(lead.status, LeadStatus.QUALIFIED)
        activity = LeadActivity.objects.get(lead=lead, activity_type="status_change")
        self.assertEqual(activity.details["to"], LeadStatus.QUALIFIED)

    def test_qualification_never_moves_a_lead_backwards_or_out_of_closed(self):
        lead = self.say("hotel in Goa")
        for status in (LeadStatus.INTERESTED, LeadStatus.LOST, LeadStatus.CONVERTED):
            Lead.objects.filter(pk=lead.pk).update(status=status)
            lead = self.say("for 2 people from 12 dec to 15 dec, ana@example.com")
            self.assertEqual(lead.status, status)

    def test_preferences_and_budget_are_copied_to_the_lead(self):
        lead = self.say("5 star hotel in Goa with a pool, budget 20000 to 50000")
        self.assertEqual(lead.preferences["hotel_stars"], 5)
        self.assertEqual(lead.preferences["amenities"], ["pool"])
        self.assertEqual(lead.preferences["product_type"], "hotel")
        self.assertNotIn("food", lead.preferences)  # blanks are dropped
        self.assertEqual(lead.budget_max, Decimal("50000"))
        self.assertEqual(lead.budget_min, Decimal("20000"))

    def test_preferences_show_on_the_conversation_and_lead_pages(self):
        lead = self.say("5 star hotel in Goa with a pool, ana@example.com")
        manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.client.force_login(manager)
        for url in (
            reverse("conversations:detail", args=[self.conversation.pk]),
            reverse("crm:lead_detail", args=[lead.pk]),
        ):
            page = self.client.get(url).content.decode()
            self.assertIn("5-star or better", page, url)
            self.assertIn("pool", page, url)


class ThrottleTests(PublicWidgetFixture):
    @override_settings(PUBLIC_API_THROTTLE_RATES={
        "widget_chat": "2/minute", "widget_poll": "2/minute",
        "widget_book": "2/minute", "public_pay": "2/minute",
    })
    def test_the_chat_endpoint_returns_429_past_the_limit(self):
        self.assertEqual(self.chat("hi").status_code, 200)
        self.assertEqual(self.chat("hi").status_code, 200)
        response = self.chat("hi")
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.json()["success"])

    @override_settings(PUBLIC_API_THROTTLE_RATES={
        "widget_chat": "100/minute", "widget_poll": "2/minute",
        "widget_book": "2/minute", "public_pay": "2/minute",
    })
    def test_history_and_book_have_their_own_limits(self):
        session, cards = self.start_with_cards()
        params = {"key": self.key.public_key, "session": session}
        codes = [self.client.get(self.history_url, params).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        codes = [self.book(session, cards[0]["recommendation_id"]).status_code for _ in range(3)]
        self.assertEqual(codes[-1], 429)

    @override_settings(PUBLIC_API_THROTTLE_RATES={
        "widget_chat": "2/minute", "widget_poll": "2/minute",
        "widget_book": "2/minute", "public_pay": "2/minute",
    })
    def test_a_client_cannot_dodge_the_limit_by_forging_forwarded_for(self):
        # Behind nginx (NUM_PROXIES=1) only the address nginx appended counts.
        codes = [
            self.chat("hi", HTTP_X_FORWARDED_FOR=f"10.0.0.{n}, 203.0.113.9").status_code
            for n in range(3)
        ]
        self.assertEqual(codes, [200, 200, 429])

    def test_staff_endpoints_are_not_throttled_this_way(self):
        from rest_framework.settings import api_settings

        self.assertEqual(api_settings.DEFAULT_THROTTLE_CLASSES, [])


class CORSTests(PublicWidgetFixture):
    def preflight(self, url, origin):
        return self.client.options(
            url,
            HTTP_ORIGIN=origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )

    def test_preflight_is_allowed_for_the_sites_domain_and_subdomains(self):
        for origin in ("https://main.com", "https://www.main.com", "http://shop.main.com"):
            response = self.preflight(self.chat_url, origin)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Access-Control-Allow-Origin"], origin)
            self.assertIn("content-type", response["Access-Control-Allow-Headers"])

    def test_other_origins_get_no_cors_headers(self):
        for origin in (
            "https://evil.com",
            "https://main.com.evil.net",
            "https://notmain.com",
            "https://main.com:8443",
            "null",
        ):
            response = self.preflight(self.chat_url, origin)
            self.assertNotIn("Access-Control-Allow-Origin", response, origin)
            response = self.chat("hi", HTTP_ORIGIN=origin)
            self.assertNotIn("Access-Control-Allow-Origin", response, origin)

    def test_a_real_request_is_allowed_for_its_own_website_only(self):
        response = self.chat("hi", HTTP_ORIGIN="https://www.main.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.main.com")

        # Another registered site's page can't use this site's key.
        Website.objects.create(name="Other", domain="other.com", source_identifier="other")
        response = self.chat("hi", HTTP_ORIGIN="https://other.com")
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_errors_are_readable_cross_origin_too(self):
        response = self.client.post(
            self.chat_url,
            {"key": "pk_nope", "message": "hi"},
            content_type="application/json",
            HTTP_ORIGIN="https://main.com",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://main.com")

    def test_the_staff_api_never_answers_cross_origin(self):
        self.client.force_login(self.manager)
        url = reverse("api_conversations:inbox")
        self.assertNotIn("Access-Control-Allow-Origin", self.preflight(url, "https://main.com"))
        response = self.client.get(url, HTTP_ORIGIN="https://main.com")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_a_domain_with_a_port_must_match_exactly(self):
        from core.cors import origin_matches_domain

        self.assertTrue(origin_matches_domain("http://localhost:3000", "localhost:3000"))
        self.assertFalse(origin_matches_domain("http://localhost:4000", "localhost:3000"))
        self.assertFalse(origin_matches_domain("http://localhost", "localhost:3000"))
        self.assertTrue(origin_matches_domain("https://a.b.main.com", "https://www.main.com/"))
        self.assertFalse(origin_matches_domain("ftp://main.com", "main.com"))

    def test_a_disabled_widget_site_is_not_an_allowed_origin(self):
        self.website.widget_enabled = False
        self.website.save()
        self.assertNotIn(
            "Access-Control-Allow-Origin", self.preflight(self.chat_url, "https://main.com")
        )


class AssignmentTests(TestCase):
    def setUp(self):
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        self.other = User.objects.create_user("other", password="pw", role="employee")
        self.conversation, _ = services.start_conversation(website=self.website)
        services.request_handoff(conversation=self.conversation)
        self.url = reverse("conversations:assign", args=[self.conversation.pk])

    def test_a_manager_assigns_and_the_agent_is_told(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.url, {"assigned_to": self.agent.pk})
        self.assertRedirects(response, reverse("conversations:detail", args=[self.conversation.pk]))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_to, self.agent)
        self.assertTrue(
            Notification.objects.filter(recipient=self.agent, title="Chat assigned to you")
        )
        entry = AuditLog.objects.get(action="conversation.assign")
        self.assertEqual(entry.metadata, {"from": None, "to": "agent"})
        note = self.conversation.messages.last()
        self.assertTrue(note.is_internal)

    def test_reassigning_from_the_inbox_row_returns_to_the_inbox(self):
        services.assign_conversation(
            conversation=self.conversation, user=self.agent, actor=self.manager
        )
        self.client.force_login(self.manager)
        inbox = self.client.get(reverse("conversations:inbox")).content.decode()
        self.assertIn(self.url, inbox)
        response = self.client.post(self.url, {"assigned_to": self.other.pk, "next": "inbox"})
        self.assertRedirects(response, reverse("conversations:inbox"))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_to, self.other)

    def test_employees_cannot_assign(self):
        self.client.force_login(self.agent)
        response = self.client.post(self.url, {"assigned_to": self.agent.pk})
        self.assertRedirects(response, reverse("dashboard:overview"))
        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.assigned_to)
        inbox = self.client.get(reverse("conversations:inbox")).content.decode()
        self.assertNotIn(self.url, inbox)

    def test_only_sales_staff_can_be_picked(self):
        stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.client.force_login(self.manager)
        self.client.post(self.url, {"assigned_to": stock.pk})
        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.assigned_to)


class StaffBookingFromChatTests(PublicWidgetFixture):
    def setUp(self):
        super().setUp()
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        session, cards = self.start_with_cards()
        self.rec_id = cards[0]["recommendation_id"]
        self.conversation = Conversation.objects.get(session_key=session)
        self.chat("my email is ana@example.com", session=session)
        services.take_over(conversation=self.conversation, user=self.agent)
        self.client.force_login(self.agent)
        self.url = reverse("conversations:book", args=[self.conversation.pk])

    def test_an_agent_books_a_recommendation_for_the_customer(self):
        start = timezone.localdate() + timedelta(days=5)
        page = self.client.get(reverse("conversations:detail", args=[self.conversation.pk]))
        self.assertContains(page, "Book this for the customer")
        response = self.client.post(self.url, {
            "recommendation_id": self.rec_id,
            "travel_start": start.isoformat(),
            "travel_end": (start + timedelta(days=2)).isoformat(),
            "travelers": 2,
        })
        booking = Booking.objects.get()
        self.assertRedirects(response, reverse("bookings:detail", args=[booking.pk]))
        self.assertEqual(booking.subtotal, Decimal("6000.00"))
        self.assertEqual(booking.created_by, self.agent)
        self.assertTrue(booking.payment_token)
        note = self.conversation.messages.filter(sender_type=MessageSender.SYSTEM).last()
        self.assertIn("agent booked Palm Stay", note.content)

    def test_another_agents_chat_is_out_of_reach(self):
        self.client.force_login(User.objects.create_user("x", password="pw", role="employee"))
        start = timezone.localdate() + timedelta(days=5)
        response = self.client.post(self.url, {
            "recommendation_id": self.rec_id,
            "travel_start": start.isoformat(),
            "travel_end": (start + timedelta(days=2)).isoformat(),
            "travelers": 2,
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Booking.objects.exists())
