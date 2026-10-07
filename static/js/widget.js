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
 * Endpoints (all keyed by the public widget key, no login):
 *   POST /api/v1/conversations/widget/chat/     send a message
 *   GET  /api/v1/conversations/widget/history/  restore the chat / poll for new messages
 *   POST /api/v1/conversations/widget/book/     "Book this" on a recommendation
 *
 * The page must be on the website's registered domain (or a subdomain of it):
 * the API only answers cross-origin requests from there.
 *
 * Every server or visitor string is rendered with textContent — never innerHTML.
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
  // The colour goes into a stylesheet: accept a plain hex value only.
  if (!/^#[0-9a-fA-F]{3,8}$/.test(COLOR)) COLOR = "#0F766E";

  var HUMAN_STATUSES = { waiting: true, human_active: true };
  var POLL_MIN = 3000;
  var POLL_MAX = 30000;

  var state = {
    session: null,
    status: "ai_active",
    lastMessageId: 0,
    historyLoaded: false,
    known: {},
    agentNoticeShown: false,
    awaitingPayment: false,
    ownBookings: {},
    // Message ids already on screen: a reply can arrive by poll and by POST.
    seen: {},
    pollTimer: null,
    pollDelay: POLL_MIN,
    polling: false,
  };

  try {
    state.session = window.localStorage.getItem(STORAGE_KEY);
  } catch (e) {
    // Private browsing: fall back to a per-page-load session.
  }

  // ---------- DOM helpers ----------

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    var name;
    for (name in attrs || {}) {
      if (!Object.prototype.hasOwnProperty.call(attrs, name)) continue;
      if (name === "className") node.className = attrs[name];
      else if (name === "text") node.textContent = attrs[name];
      else node.setAttribute(name, attrs[name]);
    }
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
  }

  function safeUrl(value) {
    return typeof value === "string" && /^https?:\/\//i.test(value) ? value : "";
  }

  function money(currency, amount) {
    var value = Number(amount);
    if (!amount || isNaN(value)) return "price on request";
    return (currency || "INR") + " " + Math.round(value).toLocaleString("en-IN");
  }

  function today() {
    var now = new Date();
    var month = String(now.getMonth() + 1);
    var day = String(now.getDate());
    return now.getFullYear() + "-" + (month.length < 2 ? "0" : "") + month + "-" + (day.length < 2 ? "0" : "") + day;
  }

  // ---------- markup ----------

  var titleNode = el("span", { text: TITLE });
  var closeBtn = el("button", { className: "scared-close", type: "button", "aria-label": "Close chat", text: "×" });
  var log = el("div", { className: "scared-log", role: "log", "aria-live": "polite" });
  var input = el("input", {
    className: "scared-input",
    placeholder: "Ask about your trip…",
    autocomplete: "off",
    "aria-label": "Your message",
    maxlength: "2000",
    required: "required",
  });
  var sendBtn = el("button", { className: "scared-send", type: "submit", text: "Send" });
  var form = el("form", { className: "scared-form" }, [input, sendBtn]);
  var panel = el("div", { className: "scared-panel", role: "dialog", "aria-label": TITLE }, [
    el("header", { className: "scared-head" }, [titleNode, closeBtn]),
    log,
    form,
  ]);
  panel.hidden = true;
  var launcher = el("button", { className: "scared-launcher", type: "button", "aria-label": "Open chat", text: "💬" });
  var root = el("div", { className: "scared-widget" }, [launcher, panel]);

  var style = document.createElement("style");
  style.textContent = [
    ".scared-widget{position:fixed;bottom:20px;right:20px;z-index:2147483000;",
    "font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.45;color:#14201b}",
    ".scared-widget *{box-sizing:border-box}",
    ".scared-launcher{width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;",
    "background:" + COLOR + ";color:#fff;font-size:24px;box-shadow:0 6px 18px rgba(0,0,0,.22)}",
    ".scared-panel{position:absolute;bottom:70px;right:0;width:360px;max-width:calc(100vw - 40px);",
    "height:520px;max-height:calc(100vh - 120px);background:#fff;border-radius:14px;overflow:hidden;",
    "display:flex;flex-direction:column;box-shadow:0 16px 44px rgba(0,0,0,.24)}",
    ".scared-panel[hidden]{display:none}",
    ".scared-head{background:" + COLOR + ";color:#fff;padding:10px 8px 10px 14px;font-weight:600;",
    "display:flex;justify-content:space-between;align-items:center}",
    ".scared-close{background:none;border:0;color:#fff;font-size:24px;cursor:pointer;line-height:1;",
    "min-width:40px;min-height:40px}",
    ".scared-log{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:12px;background:#f7faf9;",
    "display:flex;flex-direction:column;gap:8px}",
    ".scared-msg{max-width:85%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-break:break-word}",
    ".scared-msg.them{align-self:flex-start;background:#fff;border:1px solid #e2e8e5}",
    ".scared-msg.me{align-self:flex-end;background:" + COLOR + ";color:#fff}",
    ".scared-msg.note{align-self:center;background:#eceff0;color:#5d6f67;font-size:12px;text-align:center}",
    ".scared-msg small{display:block;font-size:11px;opacity:.7;margin-bottom:2px}",
    ".scared-card{align-self:flex-start;background:#fff;border:1px solid #e2e8e5;border-radius:10px;",
    "padding:9px 11px;width:85%}",
    ".scared-card b{display:block}.scared-card .scared-meta{display:block;color:#5d6f67;font-size:12px}",
    ".scared-btn{display:inline-block;margin-top:8px;background:" + COLOR + ";color:#fff;border:0;",
    "border-radius:8px;padding:8px 12px;cursor:pointer;font:inherit;font-weight:600;text-decoration:none;min-height:36px}",
    ".scared-btn.ghost{background:#fff;color:" + COLOR + ";border:1px solid " + COLOR + "}",
    ".scared-btn[disabled]{opacity:.6;cursor:not-allowed}",
    ".scared-book{display:flex;flex-direction:column;gap:6px;margin-top:8px}",
    ".scared-book label{display:flex;flex-direction:column;font-size:12px;color:#5d6f67;gap:2px}",
    ".scared-book input{padding:8px 9px;border:1px solid #d7e2dc;border-radius:8px;font:inherit;font-size:16px;color:#14201b;width:100%}",
    ".scared-book .scared-row{display:flex;gap:6px}.scared-book .scared-row label{flex:1;min-width:0}",
    ".scared-error{color:#b42318;font-size:12px}",
    ".scared-form{display:flex;gap:6px;padding:10px;border-top:1px solid #e2e8e5;background:#fff}",
    ".scared-input{flex:1;min-width:0;padding:9px 11px;border:1px solid #d7e2dc;border-radius:8px;font:inherit;font-size:16px}",
    ".scared-send{background:" + COLOR + ";color:#fff;border:0;border-radius:8px;padding:9px 14px;",
    "cursor:pointer;font:inherit}",
    ".scared-send[disabled]{opacity:.6;cursor:not-allowed}",
    // Phones: the open chat takes the whole screen, clear of notches and home bars.
    "@media (max-width:480px){",
    ".scared-widget{bottom:16px;right:16px}",
    ".scared-widget.is-open .scared-launcher{display:none}",
    ".scared-panel{position:fixed;top:0;left:0;right:0;bottom:0;width:100vw;max-width:none;",
    "height:100vh;height:100dvh;max-height:none;border-radius:0;",
    "padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}",
    ".scared-close{min-width:48px;min-height:48px;font-size:28px}",
    ".scared-send,.scared-input{min-height:48px}",
    ".scared-btn{min-height:44px;padding:10px 14px}",
    ".scared-book input{min-height:44px}",
    "}",
  ].join("");

  document.head.appendChild(style);
  document.body.appendChild(root);

  // ---------- rendering ----------

  function scrollDown() {
    log.scrollTop = log.scrollHeight;
  }

  function append(text, kind, senderName) {
    var node = el("div", { className: "scared-msg " + kind });
    if (senderName) node.appendChild(el("small", { text: senderName }));
    node.appendChild(document.createTextNode(text));
    log.appendChild(node);
    scrollDown();
    return node;
  }

  function appendCard(item) {
    var priceText = money(item.currency, item.price);
    if (item.price && item.price_label) priceText += " " + item.price_label;
    var node = el("div", { className: "scared-card" }, [
      el("b", { text: item.title }),
      el("span", { className: "scared-meta", text: [item.detail, priceText].filter(Boolean).join(" · ") }),
    ]);
    if (item.bookable && item.recommendation_id) {
      var button = el("button", { className: "scared-btn", type: "button", text: "Book this" });
      button.addEventListener("click", function () {
        button.hidden = true;
        node.appendChild(bookingForm(item, function () {
          button.hidden = false;
        }));
        scrollDown();
      });
      node.appendChild(button);
    }
    log.appendChild(node);
    scrollDown();
  }

  function appendBooking(booking, intro) {
    var lines = [intro || "Booking " + booking.number + " created."];
    if (booking.total) lines.push("Total " + money(booking.currency, booking.total) + " incl. taxes.");
    var node = el("div", { className: "scared-card" }, [
      el("b", { text: booking.product || "Your booking" }),
      el("span", { className: "scared-meta", text: lines.join(" ") }),
    ]);
    var url = safeUrl(booking.payment_url);
    if (url) {
      node.appendChild(el("a", {
        className: "scared-btn",
        href: url,
        target: "_blank",
        rel: "noopener noreferrer",
        text: "Pay now",
      }));
      state.awaitingPayment = true;
    }
    log.appendChild(node);
    scrollDown();
  }

  function renderMessage(message, initial) {
    if (state.seen[message.id]) return;
    state.seen[message.id] = true;
    state.lastMessageId = Math.max(state.lastMessageId, message.id);
    if (message.sender_type === "customer") {
      // Our own messages are already on screen, except when restoring a chat.
      if (initial) append(message.content, "me");
      return;
    }
    if (message.event === "booking_confirmed") state.awaitingPayment = false;
    if (message.booking && message.event === "booking_created") {
      if (!initial && state.ownBookings[message.booking.number]) return;
      if (message.booking.payment_url) {
        appendBooking(message.booking, message.content);
        return;
      }
    }
    if (message.sender_type === "system") {
      append(message.content, "note");
    } else {
      append(message.content, "them", message.sender_type === "agent" ? message.sender_name : "");
    }
    (message.cards || []).forEach(appendCard);
  }

  function applyStatus(data, hadReply) {
    if (!data || !data.status) return;
    state.status = data.status;
    if (data.known) state.known = data.known;
    if (HUMAN_STATUSES[state.status]) {
      if (!state.agentNoticeShown && !hadReply) {
        append("A travel consultant will reply shortly.", "note");
      }
      state.agentNoticeShown = true;
    } else {
      state.agentNoticeShown = false;
    }
    schedulePoll(false);
  }

  // ---------- booking form ----------

  function field(label, name, type, value, attrs) {
    var inputNode = el("input", { name: name, type: type, "aria-label": label });
    var key;
    for (key in attrs || {}) {
      if (Object.prototype.hasOwnProperty.call(attrs, key)) inputNode.setAttribute(key, attrs[key]);
    }
    if (value !== undefined && value !== null && value !== "") inputNode.value = String(value);
    var error = el("span", { className: "scared-error" });
    return { label: el("label", { text: label }, [inputNode, error]), input: inputNode, error: error };
  }

  function bookingForm(item, onCancel) {
    var known = state.known || {};
    var fields = {};
    var formNode = el("form", { className: "scared-book", novalidate: "novalidate" });
    var startLabel = { hotel: "Check-in", car: "Pick-up date" }[item.type] || "Start date";
    var endLabel = { hotel: "Check-out", car: "Drop-off date" }[item.type];

    if (!known.name) fields.name = field("Your name", "name", "text", "", { autocomplete: "name", maxlength: "100" });
    if (!known.email) fields.email = field("Email", "email", "email", "", { autocomplete: "email" });
    if (!known.phone) fields.phone = field("Phone", "phone", "tel", "", { autocomplete: "tel", maxlength: "20" });
    fields.travel_start = field(startLabel, "travel_start", "date", known.travel_start, { min: today() });
    if (endLabel) fields.travel_end = field(endLabel, "travel_end", "date", known.travel_end, { min: today() });
    fields.travelers = field("Travellers", "travelers", "number", known.travelers || 2, { min: "1", max: "50" });

    ["name", "email", "phone"].forEach(function (name) {
      if (fields[name]) formNode.appendChild(fields[name].label);
    });
    var row = el("div", { className: "scared-row" }, [fields.travel_start.label, fields.travel_end && fields.travel_end.label]);
    formNode.appendChild(row);
    formNode.appendChild(fields.travelers.label);

    var formError = el("span", { className: "scared-error" });
    var submit = el("button", { className: "scared-btn", type: "submit", text: "Confirm and get payment link" });
    var cancel = el("button", { className: "scared-btn ghost", type: "button", text: "Cancel" });
    formNode.appendChild(formError);
    formNode.appendChild(el("div", { className: "scared-row" }, [submit, cancel]));

    cancel.addEventListener("click", function () {
      formNode.parentNode.removeChild(formNode);
      onCancel();
    });

    formNode.addEventListener("submit", function (event) {
      event.preventDefault();
      var payload = { key: KEY, session: state.session || "", recommendation_id: item.recommendation_id };
      Object.keys(fields).forEach(function (name) {
        fields[name].error.textContent = "";
        payload[name] = fields[name].input.value.trim();
      });
      formError.textContent = "";
      submit.disabled = true;

      request("POST", "/api/v1/conversations/widget/book/", payload)
        .then(function (body) {
          if (!body.success) {
            var detail = (body.error && body.error.detail) || {};
            var shown = false;
            Object.keys(fields).forEach(function (name) {
              if (detail[name] && detail[name].length) {
                fields[name].error.textContent = String(detail[name][0]);
                shown = true;
              }
            });
            if (!shown) formError.textContent = body.message || "Please check the details.";
            return;
          }
          var data = body.data;
          state.ownBookings[data.booking.number] = true;
          formNode.parentNode.removeChild(formNode);
          data.booking.payment_url = data.payment_url;
          appendBooking(
            data.booking,
            (data.created ? "Booking " : "You already have booking ") + data.booking.number +
              (data.booking.pricing ? " · " + data.booking.pricing + "." : ".")
          );
          applyStatus(data, true);
        })
        .catch(function () {
          formError.textContent = "We could not reach the booking service. Please try again.";
        })
        .then(function () {
          submit.disabled = false;
        });
    });
    return formNode;
  }

  // ---------- network ----------

  function request(method, path, payload) {
    var options = { method: method, headers: {} };
    var url = API + path;
    if (method === "GET") {
      url += "?" + Object.keys(payload).map(function (name) {
        return encodeURIComponent(name) + "=" + encodeURIComponent(payload[name]);
      }).join("&");
    } else {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    return fetch(url, options).then(function (response) {
      if (response.status === 429) {
        return { success: false, throttled: true, message: "You are sending messages very quickly — please wait a moment." };
      }
      return response.json();
    });
  }

  function saveSession(value) {
    state.session = value;
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (e) {
      /* ignore */
    }
  }

  function loadHistory() {
    if (!state.session) {
      state.historyLoaded = true;
      return Promise.resolve();
    }
    return request("GET", "/api/v1/conversations/widget/history/", { key: KEY, session: state.session })
      .then(function (body) {
        state.historyLoaded = true;
        if (!body.success || !body.data) {
          // The chat is gone (e.g. another site's session): start afresh.
          if (!body.throttled) saveSession("");
          return;
        }
        var data = body.data;
        data.messages.forEach(function (message) {
          renderMessage(message, true);
        });
        (data.bookings || []).forEach(function (booking) {
          if (booking.payment_url) state.awaitingPayment = true;
        });
        // Restored chats already said "a consultant will reply" if they needed to.
        state.agentNoticeShown = HUMAN_STATUSES[data.status] && data.messages.length > 0;
        applyStatus(data, true);
      })
      .catch(function () {
        state.historyLoaded = true;
      });
  }

  function send(text) {
    sendBtn.disabled = true;
    var thinking = append("…", "note");

    request("POST", "/api/v1/conversations/widget/chat/", { key: KEY, message: text, session: state.session || "" })
      .then(function (body) {
        thinking.parentNode.removeChild(thinking);
        if (!body.success || !body.data) {
          append(body.message || "Something went wrong. Please try again.", "note");
          return;
        }
        var data = body.data;
        saveSession(data.session);
        var fresh = data.messages || (data.reply ? [data.reply] : []);
        var drewCards = false;
        fresh.forEach(function (message) {
          if ((message.cards || []).length) drewCards = true;
          renderMessage(message, false);
        });
        if (!drewCards) (data.recommendations || []).forEach(appendCard);
        // Waiting for a person: the AI stays quiet and the server says so.
        if (data.notice) append(data.notice, "note");
        applyStatus(data, Boolean(data.notice) || fresh.length > 0);
      })
      .catch(function () {
        if (thinking.parentNode) thinking.parentNode.removeChild(thinking);
        append("We could not reach the assistant. Please try again.", "note");
      })
      .then(function () {
        sendBtn.disabled = false;
        input.focus();
      });
  }

  // ---------- polling ----------
  //
  // Only while the panel is open and the tab visible, and only when something
  // can arrive without the visitor typing: a human agent's reply, or the
  // payment confirmation for a booking. Backs off from 3s to 30s while quiet.

  function shouldPoll() {
    return Boolean(
      state.session &&
        !panel.hidden &&
        !document.hidden &&
        (HUMAN_STATUSES[state.status] || state.awaitingPayment)
    );
  }

  function schedulePoll(reset) {
    if (reset) state.pollDelay = POLL_MIN;
    if (state.pollTimer || state.polling) return;
    if (!shouldPoll()) return;
    state.pollTimer = setTimeout(pollOnce, state.pollDelay);
  }

  function stopPolling() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function pollOnce() {
    state.pollTimer = null;
    if (!shouldPoll()) return;
    state.polling = true;
    var gotNew = false;
    request("GET", "/api/v1/conversations/widget/history/", {
      key: KEY,
      session: state.session,
      since: state.lastMessageId,
    })
      .then(function (body) {
        if (!body.success || !body.data) {
          state.pollDelay = Math.min(state.pollDelay * 2, POLL_MAX);
          return;
        }
        var hadReply = false;
        body.data.messages.forEach(function (message) {
          if (message.sender_type !== "customer") {
            gotNew = true;
            if (message.sender_type !== "system") hadReply = true;
          }
          renderMessage(message, false);
        });
        if (!(body.data.bookings || []).some(function (booking) { return booking.payment_url; })) {
          state.awaitingPayment = false;
        }
        state.polling = false;
        state.pollDelay = gotNew ? POLL_MIN : Math.min(Math.round(state.pollDelay * 1.5), POLL_MAX);
        applyStatus(body.data, hadReply || state.agentNoticeShown);
      })
      .catch(function () {
        state.pollDelay = Math.min(state.pollDelay * 2, POLL_MAX);
      })
      .then(function () {
        state.polling = false;
        schedulePoll(false);
      });
  }

  // ---------- events ----------

  function openPanel() {
    panel.hidden = false;
    root.className = "scared-widget is-open";
    var ready = state.historyLoaded ? Promise.resolve() : loadHistory();
    ready.then(function () {
      if (!log.childElementCount) {
        append("Hello! Where would you like to travel?", "them");
      }
      schedulePoll(true);
    });
    input.focus();
  }

  function closePanel() {
    panel.hidden = true;
    root.className = "scared-widget";
    stopPolling();
  }

  launcher.addEventListener("click", function () {
    if (panel.hidden) openPanel();
    else closePanel();
  });

  closeBtn.addEventListener("click", closePanel);

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stopPolling();
    else schedulePoll(true);
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
