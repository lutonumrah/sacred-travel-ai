/*
 * Live updates for the staff conversation pages, by polling (no websockets).
 *
 * - Inbox: re-fetches the chat table (same filters and page) every 10s.
 * - Conversation: fetches messages newer than the last one shown every 5s,
 *   plus the chat's status, and swaps the action buttons when it changes.
 * - Both keep the sidebar's "waiting" badge current.
 *
 * Polling pauses while the tab is hidden and resumes at once when it returns.
 * HTML inserted here is rendered and escaped server-side by Django templates;
 * nothing from a message is ever built into markup in the browser.
 */
(function () {
  "use strict";

  var MAX_FAILURES = 5;

  function getJSON(url) {
    return fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
    }).then(function (response) {
      var type = response.headers.get("content-type") || "";
      // Logged out: the login page comes back as HTML. Stop quietly.
      if (!response.ok || type.indexOf("application/json") === -1) {
        throw new Error("poll failed: " + response.status);
      }
      return response.json();
    });
  }

  function poll(task, interval) {
    var timer = null;
    var running = false;
    var failures = 0;

    function schedule(delay) {
      window.clearTimeout(timer);
      timer = window.setTimeout(tick, delay);
    }

    function tick() {
      if (document.hidden || running) return;
      running = true;
      task()
        .then(
          function () {
            failures = 0;
          },
          function () {
            failures += 1;
          }
        )
        .then(function () {
          running = false;
          // Back off after errors; give up after several in a row.
          if (failures < MAX_FAILURES) schedule(interval * (failures + 1));
        });
    }

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        window.clearTimeout(timer);
      } else if (failures < MAX_FAILURES) {
        schedule(0);
      }
    });
    schedule(interval);
  }

  function setWaitingBadge(count) {
    var badge = document.getElementById("nav-waiting");
    if (!badge || typeof count !== "number") return;
    badge.textContent = String(count);
    badge.hidden = count === 0;
  }

  // ---------- inbox ----------
  var inbox = document.getElementById("inbox-live");
  if (inbox) {
    poll(function () {
      return getJSON(inbox.getAttribute("data-live-url")).then(function (data) {
        setWaitingBadge(data.waiting);
        // Never swap the table under someone picking an assignee.
        var active = document.activeElement;
        if (active && active.tagName === "SELECT" && inbox.contains(active)) return;
        inbox.innerHTML = data.html;
      });
    }, 10000);
  }

  // ---------- conversation detail ----------
  var chat = document.getElementById("chat-window");
  var liveUrl = chat && chat.getAttribute("data-live-url");
  if (liveUrl) {
    var lastId = parseInt(chat.getAttribute("data-last-id"), 10) || 0;
    var status = chat.getAttribute("data-status");

    poll(function () {
      return getJSON(liveUrl + "?since=" + lastId).then(function (data) {
        setWaitingBadge(data.waiting);
        if (data.html && data.html.trim()) {
          var nearBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
          var empty = document.getElementById("chat-empty");
          if (empty) empty.parentNode.removeChild(empty);
          chat.insertAdjacentHTML("beforeend", data.html);
          if (nearBottom) chat.scrollTop = chat.scrollHeight;
        }
        if (data.last_id > lastId) lastId = data.last_id;

        if (data.status && data.status !== status && /^[a-z_]+$/.test(data.status)) {
          status = data.status;
          var badge = document.getElementById("conversation-status");
          if (badge) {
            badge.className = "badge status-" + data.status;
            badge.textContent = data.status_display;
          }
          var actions = document.getElementById("conversation-actions");
          if (actions && data.actions_html) actions.innerHTML = data.actions_html;
          var replyBox = document.getElementById("reply-box");
          if (replyBox) replyBox.hidden = !data.can_reply;
          var closed = document.getElementById("chat-closed");
          if (closed) closed.hidden = data.can_reply;
        }
      });
    }, 5000);
  }
})();
