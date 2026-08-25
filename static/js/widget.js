/**
 * Scared Travel AI — embeddable chat widget.
 *
 * Drop this on any customer-facing site:
 *
 *   <script src="https://your-os-host/static/js/widget.js"
 *           data-scared-key="pk_..."
 *           data-scared-api="https://your-os-host"
 *           data-scared-color="#0F766E"
 *           data-scared-title="Brand name" defer></script>
 *
 * It talks to POST /api/v1/conversations/widget/chat/, which authenticates on
 * the public key rather than a session, and polls the same endpoint with GET so
 * replies typed by a human agent in the dashboard reach the customer too.
 */
(function () {
  "use strict";

  var script =
    document.currentScript ||
    document.querySelector("script[data-scared-key]");
  if (!script) return;

  var KEY = script.getAttribute("data-scared-key");
  var API = (script.getAttribute("data-scared-api") || "").replace(/\/$/, "");
  var COLOR = script.getAttribute("data-scared-color") || "#0F766E";
  var TITLE = script.getAttribute("data-scared-title") || "Travel assistant";
  var STORAGE_KEY = "scared-travel-session:" + KEY;

  if (!KEY) return;

  var session = null;
  try {
    session = window.localStorage.getItem(STORAGE_KEY);
  } catch (e) {
    // Private browsing: fall back to a per-page-load session.
  }
  var lastMessageId = 0;
  var pollTimer = null;

  // ---------- markup ----------

  var root = document.createElement("div");
  root.className = "scared-widget";
  root.innerHTML = [
    '<button class="scared-launcher" type="button" aria-label="Open chat">💬</button>',
    '<div class="scared-panel" hidden>',
    '  <header class="scared-head"><span></span>',
    '    <button class="scared-close" type="button" aria-label="Close chat">×</button></header>',
    '  <div class="scared-log" role="log" aria-live="polite"></div>',
    '  <form class="scared-form">',
    '    <input class="scared-input" placeholder="Ask about your trip…" autocomplete="off" required>',
    '    <button class="scared-send" type="submit">Send</button>',
    "  </form>",
    "</div>",
  ].join("");

  var style = document.createElement("style");
  style.textContent = [
    ".scared-widget{position:fixed;bottom:20px;right:20px;z-index:2147483000;",
    "font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.45}",
    ".scared-launcher{width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;",
    "background:" + COLOR + ";color:#fff;font-size:24px;box-shadow:0 6px 18px rgba(0,0,0,.22)}",
    ".scared-panel{position:absolute;bottom:70px;right:0;width:340px;max-width:calc(100vw - 40px);",
    "height:460px;max-height:calc(100vh - 120px);background:#fff;border-radius:14px;overflow:hidden;",
    "display:flex;flex-direction:column;box-shadow:0 16px 44px rgba(0,0,0,.24)}",
    ".scared-head{background:" + COLOR + ";color:#fff;padding:12px 14px;font-weight:600;",
    "display:flex;justify-content:space-between;align-items:center}",
    ".scared-close{background:none;border:0;color:#fff;font-size:22px;cursor:pointer;line-height:1}",
    ".scared-log{flex:1;overflow-y:auto;padding:12px;background:#f7faf9;display:flex;",
    "flex-direction:column;gap:8px}",
    ".scared-msg{max-width:82%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-break:break-word}",
    ".scared-msg.them{align-self:flex-start;background:#fff;border:1px solid #e2e8e5}",
    ".scared-msg.me{align-self:flex-end;background:" + COLOR + ";color:#fff}",
    ".scared-msg.note{align-self:center;background:#eceff0;color:#5d6f67;font-size:12px}",
    ".scared-card{align-self:flex-start;background:#fff;border:1px solid #e2e8e5;border-radius:10px;",
    "padding:8px 11px;max-width:82%}",
    ".scared-card b{display:block}.scared-card span{color:#5d6f67;font-size:12px}",
    ".scared-form{display:flex;gap:6px;padding:10px;border-top:1px solid #e2e8e5;background:#fff}",
    ".scared-input{flex:1;padding:9px 11px;border:1px solid #d7e2dc;border-radius:8px;font:inherit}",
    ".scared-send{background:" + COLOR + ";color:#fff;border:0;border-radius:8px;padding:9px 14px;",
    "cursor:pointer;font:inherit}",
    ".scared-send[disabled]{opacity:.6;cursor:not-allowed}",
  ].join("");

  document.head.appendChild(style);
  document.body.appendChild(root);

  var launcher = root.querySelector(".scared-launcher");
  var panel = root.querySelector(".scared-panel");
  var closeBtn = root.querySelector(".scared-close");
  var log = root.querySelector(".scared-log");
  var form = root.querySelector(".scared-form");
  var input = root.querySelector(".scared-input");
  var sendBtn = root.querySelector(".scared-send");
  root.querySelector(".scared-head span").textContent = TITLE;

  // ---------- helpers ----------

  function append(text, kind) {
    var node = document.createElement("div");
    node.className = "scared-msg " + kind;
    node.textContent = text;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }

  function appendCard(item) {
    var node = document.createElement("div");
    node.className = "scared-card";
    var title = document.createElement("b");
    title.textContent = item.title;
    var meta = document.createElement("span");
    var price = item.price ? item.currency + " " + Math.round(item.price) : "price on request";
    meta.textContent = [item.detail, price].filter(Boolean).join(" · ");
    node.appendChild(title);
    node.appendChild(meta);
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
  }

  function saveSession(value) {
    session = value;
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (e) {
      /* ignore */
    }
  }

  function send(text) {
    sendBtn.disabled = true;
    var thinking = append("…", "note");

    fetch(API + "/api/v1/conversations/widget/chat/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: KEY, message: text, session: session || "" }),
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (body) {
        thinking.remove();
        if (!body.success || !body.data) {
          append(body.message || "Something went wrong. Please try again.", "note");
          return;
        }
        var data = body.data;
        saveSession(data.session);
        if (data.reply) {
          append(data.reply.content, "them");
          lastMessageId = Math.max(lastMessageId, data.reply.id);
        }
        (data.recommendations || []).forEach(appendCard);
        if (data.awaiting_human) {
          append("A travel consultant is joining this chat.", "note");
          startPolling();
        }
      })
      .catch(function () {
        thinking.remove();
        append("We could not reach the assistant. Please try again.", "note");
      })
      .finally(function () {
        sendBtn.disabled = false;
        input.focus();
      });
  }

  // Once a human is involved, poll for their replies.
  function startPolling() {
    if (pollTimer || !session) return;
    pollTimer = setInterval(function () {
      fetch(
        API +
          "/api/v1/conversations/widget/chat/?key=" +
          encodeURIComponent(KEY) +
          "&session=" +
          encodeURIComponent(session) +
          "&since=" +
          lastMessageId
      )
        .then(function (response) {
          return response.json();
        })
        .then(function (body) {
          if (!body.success || !body.data) return;
          (body.data.messages || []).forEach(function (message) {
            lastMessageId = Math.max(lastMessageId, message.id);
            if (message.sender_type === "customer") return;
            append(message.content, message.sender_type === "system" ? "note" : "them");
          });
          if (body.data.status === "closed") stopPolling();
        })
        .catch(function () {
          /* keep polling; transient failures are expected */
        });
    }, 5000);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  // ---------- events ----------

  launcher.addEventListener("click", function () {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
      if (!log.childElementCount) {
        append("Hello! Where would you like to travel?", "them");
      }
      input.focus();
    }
  });

  closeBtn.addEventListener("click", function () {
    panel.hidden = true;
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    append(text, "me");
    input.value = "";
    send(text);
  });
})();
