"""AI validation: the same widget journey answered by Claude and by Gemini.

The model is stubbed at its outermost boundary only — the `anthropic.Anthropic`
SDK client class, and `urllib.request.urlopen` for Gemini's REST API — so the
prompt building, structured-output parsing and inventory guardrails all run
for real. The admin configures the provider through the AI Settings page.
"""

import io
import json
from unittest import mock

from django.urls import reverse

from bookings.models import BookingStatus
from conversations.models import ConversationStatus, MessageSender

from .base import JourneyTestCase


def model_answer(reply, ids, *, handoff=False, **requirements):
    base = {
        "destination": "Goa",
        "product_type": "hotel",
        "travel_start": "",
        "travel_end": "",
        "travel_month": "",
        "travelers": 0,
        "budget_min": 0,
        "budget_max": 0,
    }
    base.update(requirements)
    return {
        "reply": reply,
        "requirements": base,
        "recommended_ids": ids,
        "should_handoff": handoff,
        "handoff_reason": "Customer asked for a person" if handoff else "",
    }


class ProviderJourneyMixin:
    provider = ""
    model = ""

    def configure_ai(self):
        response = self.staff_post(
            self.admin,
            reverse("conversations:ai_settings"),
            {
                "enabled": "on",
                "provider": self.provider,
                "model": self.model,
                "custom_model": "",
                f"{self.provider}_api_key": "test-key-123",
                "handoff_wait_minutes": "0",
                "agent_idle_minutes": "0",
            },
        )
        self.assertRedirects(response, reverse("conversations:ai_settings"))
        page = self.staff_get(self.admin, reverse("conversations:ai_settings"))
        # Keys are write-only.
        self.assertNotContains(page, "test-key-123")

    # Subclasses: answer(payloads) context manager -> list of captured calls,
    # each {"system": str, "messages": list, "api_key": str}.

    def run_journey(self):
        self.configure_ai()
        invented = "hotel:987654"
        hidden = f"hotel:{self.hidden_hotel.pk}"
        palm = f"hotel:{self.hotel.pk}"
        answers = [
            model_answer("Palm Stay is a lovely pick for Goa.", [invented, hidden, palm], travelers=2),
            model_answer("Free cancellation up to 14 days before arrival.", []),
            model_answer("Only made-up ideas this time.", [invented]),
        ]
        with self.answer(answers) as calls:
            first = self.widget_chat("We need a hotel in Goa for 2 people")
            session = first["session"]
            policy = self.widget_chat("What is your cancellation policy?", session=session)
            third = self.widget_chat("Anything else in Goa?", session=session)

        # The model's words reach the customer...
        self.assertEqual(first["reply"]["content"], "Palm Stay is a lovely pick for Goa.")
        self.assertEqual(policy["reply"]["content"], "Free cancellation up to 14 days before arrival.")
        # ...but only inventory the rules layer retrieved: invented and hidden ids are dropped.
        self.assertEqual([card["title"] for card in first["recommendations"]], ["Palm Stay"])
        real = {"Palm Stay", "Sea Breeze Inn"}
        self.assertTrue(third["recommendations"], "falls back to real matches")
        self.assertTrue({card["title"] for card in third["recommendations"]} <= real)

        # What the model was told.
        self.assertEqual(len(calls), 3)
        system = calls[0]["system"]
        self.assertIn("Goa Escapes", system)
        self.assertIn(self.policy.content, system, "knowledge base is in the prompt")
        self.assertIn(f"id={palm}", system)
        self.assertIn("Sea Breeze Inn", system)
        self.assertNotIn("Secret Cove Resort", system, "hidden inventory never reaches the model")
        self.assertNotIn(invented, system)
        self.assertEqual({call["api_key"] for call in calls}, {"test-key-123"})
        # History is passed back on later turns.
        self.assertIn("We need a hotel in Goa for 2 people", json.dumps(calls[1]["messages"]))

        conversation = self.conversation(session)
        engines = list(
            conversation.messages.filter(sender_type=MessageSender.AI).values_list(
                "metadata__engine", flat=True
            )
        )
        self.assertEqual(engines, [self.engine] * 3)
        self.assertEqual(conversation.lead.destination, "Goa")
        self.assertEqual(conversation.lead.travelers_count, 2)

        # The model-picked card books and pays like any other.
        response = self.widget_book(session, first["recommendations"][0]["recommendation_id"])
        self.assertEqual(response.status_code, 201, response.content)
        token = conversation.bookings.get().payment_token
        self.assertEqual(self.pay_api("simulate", token).json()["data"]["status"], BookingStatus.CONFIRMED)

    def run_handoff_decision(self):
        self.configure_ai()
        with self.answer([model_answer("Let me get a colleague.", [], handoff=True)]):
            data = self.widget_chat("This is the third time I'm asking, sort it out")
        self.assertEqual(data["status"], ConversationStatus.WAITING)
        self.assertTrue(self.notifications_for(self.manager, notification_type="handoff"))

    def run_failure_falls_back(self):
        self.configure_ai()
        with self.answer(None), self.assertLogs("conversations.ai", "ERROR"):
            data = self.widget_chat("hotel in Goa")
        self.assertTrue(data["reply"]["content"])
        self.assertTrue(data["recommendations"])
        message = self.conversation(data["session"]).messages.filter(sender_type="ai").get()
        self.assertEqual(message.metadata["engine"], "rules")


class _FakeClaude:
    """Stands in for `anthropic.Anthropic`; answers with queued JSON payloads."""

    def __init__(self, answers, calls):
        self.answers, self.calls = list(answers or []), calls

    def __call__(self, *, api_key, timeout=None):
        client = mock.Mock()

        def create(**params):
            self.calls.append(
                {"system": params["system"], "messages": params["messages"], "api_key": api_key,
                 "model": params["model"]}
            )
            if not self.answers:
                raise RuntimeError("network down")
            block = mock.Mock(type="text", text=json.dumps(self.answers.pop(0)))
            return mock.Mock(content=[block], stop_reason="end_turn")

        client.messages.create.side_effect = create
        client.beta.messages.create.side_effect = create
        return client


class ClaudeJourneyTests(ProviderJourneyMixin, JourneyTestCase):
    provider = "anthropic"
    model = "claude-opus-5-5"
    engine = "claude"

    def answer(self, answers):
        calls = []
        patcher = mock.patch("anthropic.Anthropic", _FakeClaude(answers, calls))

        class _Ctx:
            def __enter__(self_inner):
                patcher.start()
                return calls

            def __exit__(self_inner, *exc):
                patcher.stop()

        return _Ctx()

    def test_claude_answers_from_inventory_and_knowledge_only(self):
        self.run_journey()

    def test_claude_can_hand_the_chat_to_a_person(self):
        self.run_handoff_decision()

    def test_a_claude_outage_falls_back_to_the_rule_engine(self):
        self.run_failure_falls_back()


class _FakeGeminiHTTP:
    """Stands in for `urllib.request.urlopen` against generativelanguage.googleapis.com."""

    def __init__(self, answers, calls):
        self.answers, self.calls = list(answers or []), calls

    def __call__(self, request, timeout=None):
        import urllib.error

        body = json.loads(request.data)
        self.calls.append(
            {
                "url": request.full_url,
                "system": body["systemInstruction"]["parts"][0]["text"],
                "messages": body["contents"],
                "api_key": request.get_header("X-goog-api-key"),
            }
        )
        if not self.answers:
            raise urllib.error.URLError("network down")
        text = json.dumps(self.answers.pop(0))
        payload = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
        response = io.BytesIO(json.dumps(payload).encode())
        wrapper = mock.MagicMock()
        wrapper.__enter__.return_value = response
        return wrapper


class GeminiJourneyTests(ProviderJourneyMixin, JourneyTestCase):
    provider = "gemini"
    model = "gemini-3.8-flash"
    engine = "gemini"

    def answer(self, answers):
        calls = []
        patcher = mock.patch("urllib.request.urlopen", _FakeGeminiHTTP(answers, calls))

        class _Ctx:
            def __enter__(self_inner):
                patcher.start()
                return calls

            def __exit__(self_inner, *exc):
                patcher.stop()

        return _Ctx()

    def test_gemini_answers_from_inventory_and_knowledge_only(self):
        self.run_journey()

    def test_gemini_calls_the_configured_model(self):
        self.configure_ai()
        with self.answer([model_answer("Hi!", [])]) as calls:
            self.widget_chat("hotel in Goa")
        self.assertTrue(
            calls[0]["url"].endswith("/models/gemini-3.8-flash:generateContent"), calls[0]["url"]
        )

    def test_gemini_can_hand_the_chat_to_a_person(self):
        self.run_handoff_decision()

    def test_a_gemini_outage_falls_back_to_the_rule_engine(self):
        self.run_failure_falls_back()
