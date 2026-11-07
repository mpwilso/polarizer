// The card's page (docs/CARD-SPEC.md, sections 7 and 9). It asks the card for the state every
// 1.5 s, shows each open hold, and sends Allow and Deny. With each state it asks for the review
// (docs/SCAN-SPEC.md, section 6): the tool definitions waiting for approval, their flags and
// diffs, and sends Approve for one, or for the group of unflagged ones. Every text it shows was built by the
// card; it is placed with textContent, never parsed as HTML. No inline script or style: the
// Content-Security-Policy is default-src 'self'.
"use strict";

(function () {
  var POLL_MS = 1500;
  var nonce = document.querySelector('meta[name="polarizer-nonce"]').getAttribute("content");
  var queue = document.getElementById("queue");
  var empty = document.getElementById("empty");
  var endedList = document.getElementById("ended");
  var endedEmpty = document.getElementById("ended-empty");
  var problem = document.getElementById("problem");
  var stale = document.getElementById("stale");
  var result = document.getElementById("result");
  var resultLines = document.getElementById("result-lines");
  var status = document.getElementById("status");
  var definitions = document.getElementById("definitions");
  var reviewEmpty = document.getElementById("review-empty");
  var groupBox = document.getElementById("group");
  var groupButton = document.getElementById("group-approve");
  var groupNames = document.getElementById("group-names");
  var groupId = null;
  var approveButtons = [];
  var cards = {};
  var stopped = false;
  var busy = false;

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function fill(node, lines) {
    clear(node);
    lines.forEach(function (line) {
      node.appendChild(make("div", "line", line));
    });
  }

  function setButtons() {
    Object.keys(cards).forEach(function (hold) {
      var record = cards[hold];
      record.allow.disabled = busy || stopped || !record.canAllow;
      record.deny.disabled = busy || stopped;
    });
    approveButtons.forEach(function (item) {
      item.button.disabled = busy || stopped || !item.canApprove;
    });
    groupButton.disabled = busy || stopped || groupId === null;
  }

  function section(parent, label, lines, className) {
    if (!lines.length) {
      return;
    }
    parent.appendChild(make("h4", "label", label));
    var box = make("pre", className);
    box.textContent = lines.join("\n");
    parent.appendChild(box);
  }

  function renderReview(review) {
    clear(definitions);
    approveButtons = [];
    var defs = review.status === "intact" ? review.definitions : [];
    defs.forEach(function (data) {
      var box = make("article", data.flags.length ? "definition flagged" : "definition");
      box.appendChild(make("h3", null, data.name));
      fill(box.appendChild(make("div", "header")), data.header);
      if (data.flags.length) {
        var flags = make("div", "flags");
        flags.appendChild(make("p", "flag-note", "Flagged: approve it on its own, after reading it."));
        data.flags.forEach(function (line) {
          flags.appendChild(make("div", "line", line));
        });
        box.appendChild(flags);
      }
      section(box, "What changed", data.diff, "block");
      section(box, "The definition the agent would be shown", data.definition, "block");
      var button = make("button", "allow", "Approve this definition");
      button.type = "button";
      button.addEventListener("click", function () {
        approve({ upstream: data.upstream, tool: data.tool, def_hash: data.def_hash });
      });
      var actions = make("div", "actions");
      actions.appendChild(button);
      box.appendChild(actions);
      approveButtons.push({ button: button, canApprove: data.can_approve });
      definitions.appendChild(box);
    });
    reviewEmpty.hidden = defs.length > 0;
    var group = review.status === "intact" ? review.group : null;
    groupId = group ? group.id : null;
    groupBox.hidden = !group;
    clear(groupNames);
    if (group) {
      groupButton.textContent = "Approve all unflagged (" + group.count + ")";
      group.names.forEach(function (name) {
        groupNames.appendChild(make("li", null, name));
      });
    }
    setButtons();
  }

  function build(data) {
    var article = make("article", "card");
    article.setAttribute("data-hold", data.hold);
    var head = make("header", "card-head");
    head.appendChild(make("h3", "tool", data.tool));
    head.appendChild(make("p", "hold-id", "hold " + data.hold));
    article.appendChild(head);
    var rec = make("p", "recommendation");
    article.appendChild(rec);
    var notes = make("div", "notes");
    article.appendChild(notes);
    var rows = make("dl", "rows");
    article.appendChild(rows);
    article.appendChild(make("h4", "block-label", "The exact call, as polarizer holds prints it"));
    var block = make("pre", "block");
    article.appendChild(block);
    var actions = make("div", "actions");
    var label = make("label", "reason-label", "Reason for a deny (optional)");
    var reason = make("input", "reason");
    reason.type = "text";
    reason.maxLength = 1024;
    label.appendChild(reason);
    actions.appendChild(label);
    var buttons = make("div", "buttons");
    var allow = make("button", "allow", "Allow");
    allow.type = "button";
    var deny = make("button", "deny", "Deny");
    deny.type = "button";
    buttons.appendChild(allow);
    buttons.appendChild(deny);
    actions.appendChild(buttons);
    article.appendChild(actions);
    var record = {
      article: article,
      rec: rec,
      notes: notes,
      rows: rows,
      block: block,
      allow: allow,
      deny: deny,
      reason: reason,
      canAllow: true
    };
    allow.addEventListener("click", function () {
      decide(data.hold, "allow", null);
    });
    deny.addEventListener("click", function () {
      decide(data.hold, "deny", reason.value);
    });
    return record;
  }

  function update(record, data) {
    var deny = data.recommendation.decision === "deny";
    record.rec.className = deny ? "recommendation deny" : "recommendation";
    record.rec.textContent = "Recommendation, from Polarizer's code: " + data.recommendation.text;
    fill(record.notes, data.notes);
    clear(record.rows);
    data.rows.forEach(function (row) {
      record.rows.appendChild(make("dt", null, row[0]));
      var value = make("dd");
      fill(value, row[1]);
      record.rows.appendChild(value);
    });
    record.block.textContent = data.block.join("\n");
    record.canAllow = data.can_allow;
  }

  function render(state) {
    var intact = state.status === "intact";
    if (intact) {
      problem.hidden = true;
    } else {
      fill(problem, state.problem.concat(["Run polarizer verify."]));
      problem.hidden = false;
    }
    var seen = {};
    state.open.forEach(function (data) {
      seen[data.hold] = true;
      var record = cards[data.hold];
      if (!record) {
        record = build(data);
        cards[data.hold] = record;
        queue.appendChild(record.article);
      }
      update(record, data);
    });
    Object.keys(cards).forEach(function (hold) {
      if (!seen[hold]) {
        queue.removeChild(cards[hold].article);
        delete cards[hold];
      }
    });
    empty.hidden = !intact || state.open.length > 0;
    clear(endedList);
    state.ended.forEach(function (item) {
      var entry = make("li", null, item.tool + ", hold " + item.hold + ": " + item.text);
      (item.reveal || []).forEach(function (line) {
        entry.appendChild(make("div", "line", line));
      });
      endedList.appendChild(entry);
    });
    endedEmpty.hidden = state.ended.length > 0;
    setButtons();
  }

  function goStale() {
    stopped = true;
    stale.hidden = false;
    status.textContent = "Stopped.";
    setButtons();
  }

  function showResult(lines, failed) {
    resultLines.textContent = lines.join("\n");
    result.className = failed ? "result error" : "result";
    result.hidden = false;
  }

  function poll() {
    if (stopped) {
      return;
    }
    fetch("/state", {
      headers: { "X-Polarizer-Nonce": nonce },
      cache: "no-store",
      credentials: "same-origin"
    })
      .then(function (response) {
        if (response.status === 403) {
          goStale();
          return null;
        }
        if (!response.ok) {
          throw new Error("status " + response.status);
        }
        return response.json();
      })
      .then(function (state) {
        if (state) {
          render(state);
          status.textContent = "Checked at " + new Date().toLocaleTimeString() + ".";
          return fetch("/review", {
            headers: { "X-Polarizer-Nonce": nonce },
            cache: "no-store",
            credentials: "same-origin"
          }).then(function (response) {
            if (response.status === 403) {
              goStale();
              return null;
            }
            return response.ok ? response.json() : null;
          });
        }
        return null;
      })
      .then(function (review) {
        if (review) {
          renderReview(review);
        }
      })
      .catch(function () {
        status.textContent = "Cannot reach the card; trying again.";
      })
      .then(function () {
        if (!stopped) {
          window.setTimeout(poll, POLL_MS);
        }
      });
  }

  function decide(hold, decision, reason) {
    if (busy || stopped) {
      return;
    }
    busy = true;
    setButtons();
    fetch("/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Polarizer-Nonce": nonce },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify({
        hold: hold,
        decision: decision,
        reason: decision === "deny" && reason ? reason : null
      })
    })
      .then(function (response) {
        if (response.status === 403) {
          goStale();
          return null;
        }
        return response.json();
      })
      .then(function (answer) {
        if (!answer) {
          return;
        }
        if (answer.ok) {
          showResult(answer.lines.concat(answer.warnings, answer.reveal || []), false);
        } else {
          showResult([answer.error], true);
        }
      })
      .catch(function () {
        showResult(["The card did not answer. Run polarizer holds before deciding again."], true);
      })
      .then(function () {
        busy = false;
        setButtons();
      });
  }

  function approve(body) {
    if (busy || stopped) {
      return;
    }
    busy = true;
    setButtons();
    fetch("/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Polarizer-Nonce": nonce },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify(body)
    })
      .then(function (response) {
        if (response.status === 403) {
          goStale();
          return null;
        }
        return response.json();
      })
      .then(function (answer) {
        if (!answer) {
          return;
        }
        if (answer.ok) {
          showResult(answer.lines, false);
        } else {
          showResult((answer.lines || []).concat([answer.error]), true);
        }
      })
      .catch(function () {
        showResult(["The card did not answer. Run polarizer pending before approving again."], true);
      })
      .then(function () {
        busy = false;
        setButtons();
      });
  }

  groupButton.addEventListener("click", function () {
    if (groupId !== null) {
      approve({ group: groupId });
    }
  });

  poll();
})();
