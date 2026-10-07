"""Human takeover: the customer asks for a person, an agent answers from the inbox."""

from datetime import timedelta

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from conversations.models import (
    AISettings,
    Conversation,
    ConversationHandoff,
    ConversationStatus,
    Message,
    MessageSender,
)
from conversations.services import AUTO_RESUME_MESSAGES

from .base import JourneyTestCase


class HandoffJourneyTests(JourneyTestCase):
    def test_customer_to_agent_and_back_to_the_ai(self):
        # The AI is answering.
        first = self.widget_chat("hotel in Goa for 2 people, ana@example.com")
        session = first["session"]
        conversation = self.conversation(session)

        # 1. The customer asks for a person: the chat waits and managers are told.
        asked = self.widget_chat("Can I talk to a human please?", session=session)
        self.assertEqual(asked["status"], ConversationStatus.WAITING)
        self.assertTrue(asked["awaiting_human"])
        self.assertTrue(
            self.notifications_for(self.manager, notification_type="handoff").filter(
                metadata__conversation_id=conversation.pk
            )
        )
        self.assertTrue(self.outbox_to(self.manager.email, "[Handoff]"))

        # While waiting the AI keeps quiet and says a consultant is coming.
        quiet = self.widget_chat("hello?", session=session)
        self.assertIsNone(quiet["reply"])
        self.assertIn("consultant", quiet["notice"])

        # 2. The agent sees it in the inbox, and in the live refresh.
        inbox = self.staff_get(self.agent, reverse("conversations:inbox"))
        self.assertContains(inbox, reverse("conversations:detail", args=[conversation.pk]))
        live = self.staff_get(self.agent, reverse("conversations:inbox_live")).json()
        self.assertEqual(live["waiting"], 1)
        self.assertIn(reverse("conversations:detail", args=[conversation.pk]), live["html"])

        # 3. Take over and reply.
        detail_url = reverse("conversations:detail", args=[conversation.pk])
        response = self.staff_post(
            self.agent, reverse("conversations:take_over", args=[conversation.pk]), {"reason": ""}
        )
        self.assertRedirects(response, detail_url)
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, ConversationStatus.HUMAN_ACTIVE)
        self.assertEqual(conversation.assigned_to, self.agent)
        last_seen = Message.objects.filter(conversation=conversation).latest("pk").pk
        self.staff_post(
            self.agent,
            reverse("conversations:reply", args=[conversation.pk]),
            {"content": "Hi Ana, Ravi here. I can hold Palm Stay for you."},
        )

        # 4. The widget's poll delivers it, named by first name only.
        polled = self.widget_history(session, since=last_seen)
        self.assertEqual(polled["status"], ConversationStatus.HUMAN_ACTIVE)
        agent_rows = [m for m in polled["messages"] if m["sender_type"] == MessageSender.AGENT]
        self.assertEqual(len(agent_rows), 1)
        self.assertEqual(agent_rows[0]["sender_name"], "Ravi")
        self.assertIn("Ravi here", agent_rows[0]["content"])
        self.assertNotIn(self.agent.username, str(self.widget_history(session)))

        # 5. Customer replies; the agent sees it on the live conversation page.
        mark = Message.objects.filter(conversation=conversation).latest("pk").pk
        answered = self.widget_chat("Yes please hold it", session=session)
        self.assertIsNone(answered["reply"])
        self.assertEqual(answered["notice"], "")
        live_chat = self.staff_get(
            self.agent, reverse("conversations:live", args=[conversation.pk]), {"since": mark}
        ).json()
        self.assertIn("Yes please hold it", live_chat["html"])
        self.assertEqual(live_chat["status"], ConversationStatus.HUMAN_ACTIVE)
        ping = self.notifications_for(self.agent, title="New customer message")
        self.assertEqual(ping.count(), 1)
        self.assertFalse(self.outbox_to(self.agent.email, "New customer message"), "in-app only")

        # 6. Hand back to the AI, which answers again.
        self.staff_post(self.agent, reverse("conversations:resume_ai", args=[conversation.pk]))
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, ConversationStatus.AI_ACTIVE)
        handoff = ConversationHandoff.objects.get(conversation=conversation)
        self.assertEqual(handoff.taken_by, self.agent)
        self.assertIsNotNone(handoff.resumed_ai_at)
        again = self.widget_chat("Any car in Goa for 2 people?", session=session)
        self.assertEqual(again["status"], ConversationStatus.AI_ACTIVE)
        self.assertIsNotNone(again["reply"])
        self.assertEqual(again["reply"]["sender_type"], MessageSender.AI)

        self.assertLessEqual(
            {
                "conversation.handoff_requested",
                "conversation.take_over",
                "conversation.resume_ai",
            },
            self.audit_actions(conversation),
        )
        # Employee analytics credit the agent with the handoff and a first response.
        analytics = self.staff_get(self.manager, reverse("api_dashboard:analytics")).json()["data"]
        row = next(r for r in analytics["employees"] if r["username"] == "agent1")
        self.assertEqual((row["handoffs"], row["responded_handoffs"]), (1, 1))
        self.assertIsNotNone(row["avg_first_response_seconds"])

        # The full transcript stays in history after closing.
        self.staff_post(self.agent, reverse("conversations:close", args=[conversation.pk]))
        history = self.staff_get(self.agent, reverse("conversations:history"))
        self.assertContains(history, detail_url)
        transcript = self.staff_get(self.agent, detail_url)
        self.assertContains(transcript, "Ravi here")
        self.assertContains(transcript, "Can I talk to a human please?")

    def test_manager_assigns_the_waiting_chat_from_the_inbox(self):
        data = self.widget_chat("I want to speak to someone")
        conversation = self.conversation(data["session"])
        self.staff_post(
            self.manager,
            reverse("conversations:assign", args=[conversation.pk]),
            {"assigned_to": self.agent_two.pk, "next": "inbox"},
        )
        conversation.refresh_from_db()
        self.assertEqual(conversation.assigned_to, self.agent_two)
        self.assertTrue(self.notifications_for(self.agent_two, title="Chat assigned to you"))
        # No longer in the other employee's inbox, and closed to them.
        self.assertNotContains(
            self.staff_get(self.agent, reverse("conversations:inbox")),
            reverse("conversations:detail", args=[conversation.pk]),
        )
        self.assertEqual(
            self.staff_post(
                self.agent, reverse("conversations:take_over", args=[conversation.pk])
            ).status_code,
            404,
        )
        # The internal assignment note never reaches the customer.
        self.assertNotIn("Assigned to", str(self.widget_history(data["session"])))

    def test_unanswered_handoff_is_resumed_by_the_scheduler(self):
        self.staff_post(
            self.admin,
            reverse("conversations:ai_settings"),
            {
                "enabled": "on",
                "provider": "anthropic",
                "model": "claude-opus-5-5",
                "custom_model": "",
                "handoff_wait_minutes": "5",
                "agent_idle_minutes": "0",
            },
        )
        self.assertEqual(AISettings.load().handoff_wait_minutes, 5)
        data = self.widget_chat("talk to a human")
        session = data["session"]
        self.assertEqual(data["status"], ConversationStatus.WAITING)

        # Nothing happens before the timer.
        with self.captureOnCommitCallbacks(execute=True):
            call_command("run_scheduled_jobs", stdout=open("/dev/null", "w"))
        self.assertEqual(self.conversation(session).status, ConversationStatus.WAITING)

        Conversation.objects.filter(session_key=session).update(
            handoff_requested_at=timezone.now() - timedelta(minutes=6)
        )
        with self.captureOnCommitCallbacks(execute=True):
            call_command("run_scheduled_jobs", stdout=open("/dev/null", "w"))
        polled = self.widget_history(session)
        self.assertEqual(polled["status"], ConversationStatus.AI_ACTIVE)
        self.assertIn(
            AUTO_RESUME_MESSAGES["handoff_wait"], [m["content"] for m in polled["messages"]]
        )
        self.assertTrue(
            self.notifications_for(self.manager, metadata__reason="handoff_wait")
        )
        self.assertIn("conversation.auto_resume", self.audit_actions(self.conversation(session)))
        # And the AI answers the next message.
        again = self.widget_chat("hotel in Goa", session=session)
        self.assertIsNotNone(again["reply"])

    def test_idle_agent_is_replaced_by_the_ai_when_the_widget_polls(self):
        config = AISettings.load()
        config.agent_idle_minutes = 10
        config.save()
        data = self.widget_chat("hotel in Goa")
        session = data["session"]
        conversation = self.conversation(session)
        self.staff_post(self.agent, reverse("conversations:take_over", args=[conversation.pk]))
        self.widget_chat("are you there?", session=session)
        old = timezone.now() - timedelta(minutes=15)
        conversation.handoffs.update(created_at=old - timedelta(minutes=1))
        Message.objects.filter(conversation=conversation, content="are you there?").update(
            created_at=old
        )
        polled = self.widget_history(session)
        self.assertEqual(polled["status"], ConversationStatus.AI_ACTIVE)
        self.assertTrue(self.notifications_for(self.agent, metadata__reason="agent_idle"))

    def test_a_customer_writing_into_a_closed_chat_reopens_it(self):
        data = self.widget_chat("hotel in Goa")
        session = data["session"]
        conversation = self.conversation(session)
        self.staff_post(self.manager, reverse("conversations:close", args=[conversation.pk]))
        self.assertEqual(self.conversation(session).status, ConversationStatus.CLOSED)

        again = self.widget_chat("Hi, I'm back - is Palm Stay still free?", session=session)
        self.assertIsNotNone(again["reply"])
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, ConversationStatus.AI_ACTIVE)
        self.assertIsNone(conversation.closed_at)
        # Back in the live inbox, so staff can see the returning customer.
        self.assertContains(
            self.staff_get(self.manager, reverse("conversations:inbox")),
            reverse("conversations:detail", args=[conversation.pk]),
        )

    def test_no_match_reply_never_promises_a_handoff_it_does_not_make(self):
        # Everything known, nothing in inventory: the rule engine's dead end.
        start = self.trip_start()
        data = self.widget_chat(
            f"package in Goa for 2 people from {start.isoformat()} to "
            f"{(start + timedelta(days=3)).isoformat()}, budget 1000"
        )
        self.assertEqual(data["recommendations"], [])
        reply = data["reply"]["content"]
        self.assertEqual(data["status"], ConversationStatus.AI_ACTIVE)
        self.assertNotIn("put you through", reply)
        # Following the reply's own instruction does bring a person in.
        self.assertIn("talk to an agent", reply)
        followed = self.widget_chat("talk to an agent", session=data["session"])
        self.assertEqual(followed["status"], ConversationStatus.WAITING)
