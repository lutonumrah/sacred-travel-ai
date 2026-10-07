/*
 * Lead pipeline board: move a card by dragging it to another column, or with
 * the status menu on the card (keyboard and touch). The card moves at once and
 * goes back where it was if the server refuses.
 */
(function () {
  "use strict";

  var board = document.getElementById("pipeline");
  if (!board) return;
  var moveUrl = board.getAttribute("data-move-url");
  var tokenInput = board.querySelector("input[name=csrfmiddlewaretoken]");
  var csrf = tokenInput ? tokenInput.value : "";
  var live = document.getElementById("pipeline-status");
  var dragged = null;

  function column(status) {
    return board.querySelector('.kanban-col[data-status="' + status + '"]');
  }

  function listOf(col) {
    return col.querySelector("[data-dropzone]");
  }

  function adjust(col, delta) {
    var badge = col.querySelector("[data-count]");
    badge.textContent = String((parseInt(badge.textContent, 10) || 0) + delta);
    col.querySelector(".kanban-empty").hidden = listOf(col).children.length > 0;
  }

  function setStatus(card, status) {
    card.setAttribute("data-status", status);
    var select = card.querySelector("select[name=status]");
    if (select) select.value = status;
  }

  function announce(text) {
    if (live) live.textContent = text;
  }

  function label(status) {
    var col = column(status);
    return col ? col.querySelector("h3").firstChild.textContent.trim() : status;
  }

  function move(card, status) {
    var from = card.getAttribute("data-status");
    var fromCol = column(from);
    var toCol = column(status);
    if (from === status || !fromCol || !toCol) return;

    var lostReason = "";
    if (status === "lost") {
      lostReason = window.prompt("Why was this lead lost? (optional)", "");
      if (lostReason === null) {
        setStatus(card, from);
        return;
      }
    }

    var originParent = card.parentNode;
    var originNext = card.nextSibling;
    listOf(toCol).insertBefore(card, listOf(toCol).firstChild);
    setStatus(card, status);
    adjust(fromCol, -1);
    adjust(toCol, 1);
    card.classList.add("is-saving");

    var body = new URLSearchParams();
    body.append("status", status);
    if (lostReason) body.append("lost_reason", lostReason);

    fetch(moveUrl.replace("/0/", "/" + card.getAttribute("data-lead") + "/"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" },
      body: body,
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!response.ok || !data.success) {
              throw new Error(data.message || "Could not move the lead. Please try again.");
            }
            return data;
          });
      })
      .then(function (data) {
        var score = card.querySelector(".score");
        if (score && data.score !== undefined) score.textContent = data.score;
        announce("Moved to " + label(status) + ".");
      })
      .catch(function (error) {
        // Put the card back exactly where it was.
        if (originNext && originNext.parentNode === originParent) {
          originParent.insertBefore(card, originNext);
        } else {
          originParent.appendChild(card);
        }
        setStatus(card, from);
        adjust(toCol, -1);
        adjust(fromCol, 1);
        announce(error.message);
        window.alert(error.message);
      })
      .then(function () {
        card.classList.remove("is-saving");
      });
  }

  board.addEventListener("change", function (event) {
    var select = event.target;
    if (select.name !== "status") return;
    var card = select.closest(".kanban-card");
    if (card) move(card, select.value);
  });

  board.addEventListener("dragstart", function (event) {
    var card = event.target.closest && event.target.closest(".kanban-card");
    if (!card) return;
    dragged = card;
    card.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.getAttribute("data-lead"));
  });

  board.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("is-dragging");
    dragged = null;
    Array.prototype.forEach.call(board.querySelectorAll(".kanban-col.is-over"), function (col) {
      col.classList.remove("is-over");
    });
  });

  board.addEventListener("dragover", function (event) {
    var col = event.target.closest && event.target.closest(".kanban-col");
    if (!col || !dragged) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    col.classList.add("is-over");
  });

  board.addEventListener("dragleave", function (event) {
    var col = event.target.closest && event.target.closest(".kanban-col");
    if (col && !col.contains(event.relatedTarget)) col.classList.remove("is-over");
  });

  board.addEventListener("drop", function (event) {
    var col = event.target.closest && event.target.closest(".kanban-col");
    if (!col || !dragged) return;
    event.preventDefault();
    col.classList.remove("is-over");
    move(dragged, col.getAttribute("data-status"));
  });
})();
