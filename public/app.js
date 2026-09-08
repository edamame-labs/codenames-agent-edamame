const $ = (id) => document.getElementById(id);

let token = null;
let view = null;
let busy = false;
let autoplay = true;
let cellNodes = [];
let boardWords = [];
let prevRevealed = [];
let suggestedIndexes = [];
let lastHint = null;
let pendingAck = null;
let toastTimer = null;
let retryNeeded = false;
let endShown = false;
let lastFound = { red: 0, blue: 0 };
let hudReady = false;
let scoreFlash = null;
let reportBusy = false;

const PRESETS = {
  versus_operative: { single_team: false, seats: { red_cm: "ai", red_g: "human", blue_cm: "ai", blue_g: "ai" } },
  versus_spymaster: { single_team: false, seats: { red_cm: "human", red_g: "ai", blue_cm: "ai", blue_g: "ai" } },
  cooperative: { single_team: true, seats: { red_cm: "ai", red_g: "human", blue_cm: "ai", blue_g: "ai" } },
  spectate: { single_team: false, seats: { red_cm: "ai", red_g: "ai", blue_cm: "ai", blue_g: "ai" } },
};

const CAST = {
  you: "/assets/cast/you-operative.webp?v=20260906-apple34",
  ally_cm: "/assets/cast/ally-cm.webp?v=20260906-apple34",
  ally_g: "/assets/cast/ally-op.webp?v=20260906-apple34",
  enemy_cm: "/assets/cast/enemy-cm.webp?v=20260906-apple34",
  enemy_g: "/assets/cast/enemy-op.webp?v=20260906-apple34",
};
const HINT_MASCOT = "/assets/cast/hint.webp";

function seatTeam(seat) { return seat.startsWith("red") ? "Red" : "Blue"; }
function seatRole(seat) { return seat.endsWith("cm") ? "cm" : "g"; }
function roleLabel(seat) { return seatRole(seat) === "cm" ? "Spymaster" : "Operative"; }
function opposingTeam(team) { return team === "Blue" ? "Red" : "Blue"; }
function playerTeam() {
  if (!view || !view.seats) return null;
  if (view.seats.red_cm === "human" || view.seats.red_g === "human") return "Red";
  if (view.seats.blue_cm === "human" || view.seats.blue_g === "human") return "Blue";
  return null;
}
function turnTeam() {
  if (!view) return "Red";
  if (view.acting_seat) return seatTeam(view.acting_seat);
  return view.turn || "Red";
}
function seatKind(seat) {
  if (view && view.seats && view.seats[seat] === "human") return "you";
  const mine = playerTeam();
  const team = seatTeam(seat);
  if (mine) return team === mine ? "bot" : "enemy";
  return team === "Red" ? "bot" : "enemy";
}
function portraitFor(seat) {
  if (seatKind(seat) === "you") return CAST.you;
  const role = seatRole(seat) === "cm" ? "cm" : "g";
  const kind = seatKind(seat) === "bot" ? "ally" : "enemy";
  return CAST[kind + "_" + role] || CAST.you;
}
function kindLabel(kind) {
  return kind === "you" ? "You" : (kind === "enemy" ? "Enemy" : "Ally");
}
function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

const THINK_MS = 3000;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function holdThinking(started, ms = THINK_MS) {
  const left = ms - (Date.now() - started);
  return left > 0 ? wait(left) : Promise.resolve();
}

function setAIThinking(show, kind = "turn") {
  const overlay = $("ai-thinking");
  if (!overlay) return;
  if (!show) {
    overlay.classList.add("hidden");
    document.body.classList.remove("ai-busy");
    return;
  }

  const hintMode = kind === "hint";
  const seat = view && view.acting_seat;
  $("thinking-image").src = hintMode ? HINT_MASCOT : (seat ? portraitFor(seat) : HINT_MASCOT);
  $("thinking-badge").textContent = hintMode ? "Hint" : "AI";
  $("thinking-title").textContent = hintMode
    ? "Looking for possible matches"
    : ((seat ? roleLabel(seat) : "AI") + " is thinking");
  $("thinking-sub").textContent = hintMode
    ? "A few ideas. They might be wrong."
    : (seatRole(seat || "red_cm") === "cm" ? "Writing a clue" : "Picking the next card");
  overlay.classList.toggle("hint-mode", hintMode);
  const bar = overlay.querySelector(".thinking-progress i");
  if (bar) {
    bar.style.animation = "none";
    void bar.offsetWidth;
    bar.style.animation = "";
  }
  overlay.classList.remove("hidden");
  document.body.classList.add("ai-busy");
}

async function api(payload) {
  const res = await fetch("/api/game", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data = {};
  try {
    data = await res.json();
  } catch (err) {
    throw new Error(res.ok ? "Bad response" : "Server error " + res.status);
  }
  if (!res.ok && !data.error) data.error = "Request failed";
  return data;
}

/* ---------- board ---------- */

function buildBoard(cells) {
  const board = $("board");
  board.innerHTML = "";
  cellNodes = [];
  cells.forEach((cell, i) => {
    const card = document.createElement("button");
    card.className = "card";
    card.type = "button";
    card.innerHTML =
      '<div class="card-inner"><div class="card-front"><span class="word"></span></div>' +
      '<div class="card-back"><span class="label"></span><span class="revealed-word"></span></div></div>';
    card.querySelector(".word").textContent = cell.word;
    card.querySelector(".revealed-word").textContent = cell.word;
    card.setAttribute("aria-label", cell.word);
    if (cell.revealed) {
      card.classList.add("revealed");
      if (cell.label && BACK[cell.label]) card.classList.add(BACK[cell.label]);
      const labelEl = card.querySelector(".label");
      if (labelEl) labelEl.textContent = faceLabel(cell.label);
    } else {
      card.classList.add("deal");
      card.style.animationDelay = (i * 0.035).toFixed(3) + "s";
    }
    card.addEventListener("click", () => {
      if (card.classList.contains("clickable")) act({ type: "guess", index: i });
    });
    board.appendChild(card);
    cellNodes.push(card);
  });
  boardWords = cells.map((c) => c.word);
  prevRevealed = cells.map((c) => Boolean(c.revealed));
}

const BACK = { Red: "back-red", Blue: "back-blue", Civilian: "back-civilian", Assassin: "back-assassin" };
const HINT = { Red: "hint-red", Blue: "hint-blue", Civilian: "hint-civilian", Assassin: "hint-assassin" };

function updateBoard() {
  const clickable = view.status === "need_human_guess";
  view.cells.forEach((cell, i) => {
    const card = cellNodes[i];
    if (!card) return;
    card.classList.remove("hint-red", "hint-blue", "hint-civilian", "hint-assassin", "suggested");
    if (!cell.revealed && cell.label && HINT[cell.label]) card.classList.add(HINT[cell.label]);
    if (cell.revealed) {
      card.classList.remove("deal");
      if (cell.label && BACK[cell.label]) card.classList.add(BACK[cell.label]);
      card.querySelector(".label").textContent = faceLabel(cell.label);
      if (!prevRevealed[i]) {
        card.classList.add("revealed");
        celebrateReveal(card, cell.label);
      }
    }
    if (suggestedIndexes.indexOf(i) !== -1 && !cell.revealed) card.classList.add("suggested");
    const selectable = clickable && !cell.revealed;
    card.classList.toggle("clickable", selectable);
    card.tabIndex = selectable ? 0 : -1;
    card.setAttribute("aria-disabled", selectable ? "false" : "true");
    card.setAttribute(
      "aria-label",
      cell.revealed
        ? cell.word + ", revealed: " + faceLabel(cell.label)
        : cell.word + (selectable ? ", available to guess" : ""),
    );
    prevRevealed[i] = cell.revealed;
  });
}

/* ---------- HUD ---------- */

function faceLabel(label) {
  if (!label) return "";
  if (!playerTeam()) return label.toUpperCase();
  if (label === playerTeam()) return "YOURS";
  if (label === "Assassin") return "KILL";
  if (label === "Civilian") return "BYSTANDER";
  return "ENEMY";
}

function celebrateReveal(card, label) {
  const mine = playerTeam();
  const playing = !!mine;
  const hit = playing && label === mine;
  const kill = label === "Assassin";
  const enemy = playing && !hit && !kill && label !== "Civilian";
  const celebrate = hit || (!playing && label === "Red");
  const kind = kill ? "just-kill" : (celebrate ? "just-hit" : "just-miss");
  card.classList.add(kind);
  card.querySelectorAll(".reveal-burst").forEach((node) => node.remove());
  const burst = document.createElement("span");
  if (!playing) {
    burst.className = "reveal-burst " + (label === "Red" ? "good" : (kill ? "dead" : (label === "Blue" ? "enemy" : "bad")));
    burst.textContent = (label || "").toUpperCase();
  } else {
    burst.className = "reveal-burst " + (hit ? "good" : (kill ? "dead" : (enemy ? "enemy" : "bad")));
    burst.textContent = hit ? "YES" : (kill ? "DEAD" : (enemy ? "ENEMY" : "BYSTANDER"));
  }
  card.appendChild(burst);
  setTimeout(() => {
    card.classList.remove("just-hit", "just-miss", "just-kill");
    burst.remove();
  }, celebrate ? 2600 : 1600);
  document.body.classList.remove("guess-hit", "guess-miss", "guess-kill", "guess-enemy");
  if (celebrate) {
    if (playing) {
      hitConfetti();
    }
    document.body.classList.add("guess-hit");
    setTimeout(() => document.body.classList.remove("guess-hit"), 2200);
  } else {
    stopConfetti();
    const bodyClass = kill ? "guess-kill" : (enemy || label === "Blue" ? "guess-enemy" : "guess-miss");
    document.body.classList.add(bodyClass);
    if (!pendingAck) {
      setTimeout(() => document.body.classList.remove("guess-miss", "guess-kill", "guess-enemy"), 1600);
    }
  }
}

function humanMissFromEvents(events) {
  const rows = events || [];
  for (let i = rows.length - 1; i >= 0; i--) {
    const match = String(rows[i] || "").match(/guesser \(you\): .+\ -> (Red|Blue|Civilian|Assassin)$/);
    if (!match) continue;
    const label = match[1];
    if (label === "Assassin") return null;
    const mine = playerTeam();
    if (mine && label === mine) return null;
    return { label };
  }
  return null;
}

function acknowledgeMiss() {
  pendingAck = null;
  document.body.classList.remove("guess-miss", "guess-kill", "guess-enemy", "await-ack");
  autoplay = true;
  render();
  if (view && view.status === "ai_turn") act(null);
}

function meterHud(el, team, left, total, userTeam) {
  const found = total - left;
  const pct = Math.max(0, Math.min(100, (found / total) * 100));
  const yours = userTeam === team;
  const toPlay = view.status !== "over" && !pendingAck && turnTeam() === team;
  const flashing = scoreFlash && scoreFlash.team === team.toLowerCase();
  let teamLine;
  if (toPlay && yours) teamLine = "YOUR TURN";
  else if (toPlay && userTeam) teamLine = "THEIR TURN";
  else if (toPlay) teamLine = team.toUpperCase() + " TURN";
  else if (yours) teamLine = "YOU ARE " + team.toUpperCase();
  else if (userTeam) teamLine = "ENEMY · " + team.toUpperCase();
  else teamLine = team.toUpperCase();
  el.className =
    "team-hud " + team.toLowerCase() +
    (yours ? " your-team" : (userTeam ? " enemy-team" : "")) +
    (toPlay ? " to-play" : " waiting") +
    (flashing ? " score-pop" : "");
  el.innerHTML =
    '<div class="score-stack">' +
      '<span class="score-team">' + teamLine + "</span>" +
      '<span class="score-line"><strong>' + found + "</strong><i>/ " + total + "</i></span>" +
    "</div>" +
    '<div class="t-bar"><div class="t-fill" style="width:' + pct + '%"></div></div>' +
    (flashing ? '<span class="score-plus">+' + scoreFlash.plus + "</span>" : "");
}

function racePressure() {
  if (!view || view.single_team || view.status === "over") return "";
  const mine = playerTeam();
  if (!mine) return "";
  const own = mine === "Red" ? view.counts.red_left : view.counts.blue_left;
  const opp = mine === "Red" ? view.counts.blue_left : view.counts.red_left;
  if (own <= opp) return "";
  if (opp > 0 && opp <= 3) return "urgent";
  return "";
}

function updateHud() {
  const team = playerTeam();
  const redFound = 9 - view.counts.red_left;
  const blueFound = view.single_team ? 0 : 8 - view.counts.blue_left;
  if (!hudReady) {
    lastFound = { red: redFound, blue: blueFound };
    hudReady = true;
  } else {
    const plusRed = redFound - lastFound.red;
    const plusBlue = blueFound - lastFound.blue;
    lastFound = { red: redFound, blue: blueFound };
    if (plusRed > 0 || plusBlue > 0) {
      clearTimeout(scoreFlash && scoreFlash.timer);
      scoreFlash = {
        team: plusRed > 0 ? "red" : "blue",
        plus: plusRed > 0 ? plusRed : plusBlue,
      };
      scoreFlash.timer = setTimeout(() => {
        scoreFlash = null;
        if (view) updateHud();
      }, 2000);
    }
  }
  meterHud($("hud-red"), "Red", view.counts.red_left, 9, team);
  const blue = $("hud-blue");
  if (view.single_team) {
    blue.className = "team-hud";
    blue.innerHTML =
      '<div class="score-stack">' +
        '<span class="score-team">CLUES</span>' +
        '<span class="score-line"><strong>' + view.red_clues + "</strong><i>used</i></span>" +
      "</div>";
  } else {
    meterHud(blue, "Blue", view.counts.blue_left, 8, team);
  }
  const pill = $("turn-pill");
  if (pill) {
    const turn = turnTeam();
    const actingHuman = view.acting_seat && view.seats && view.seats[view.acting_seat] === "human";
    if (view.status === "over") {
      pill.textContent = "Match over";
      pill.className = "turn-pill";
    } else if (pendingAck) {
      pill.textContent = "Turn over";
    pill.className = "turn-pill " + String(pendingAck.team || team || "Red").toLowerCase();
    } else {
      pill.textContent = actingHuman ? "Your turn" : turn + " turn";
      pill.className = "turn-pill " + turn.toLowerCase() + (actingHuman ? " you" : "");
    }
  }
}

function updateCommand() {
  const el = $("command");
  const kicker = $("command-kicker");
  const title = $("command-title");
  const sub = $("command-sub");
  if (!el || !kicker) return;

  const actingHuman = view.acting_seat && view.seats && view.seats[view.acting_seat] === "human";
  const actingRole = view.acting_seat ? roleLabel(view.acting_seat) : "";
  const actingTeam = turnTeam();
  const otherTeam = opposingTeam(actingTeam);
  const made = view.pending ? view.pending.made : 0;
  const number = view.pending ? view.pending.number : 0;
  const canEnd = view.status === "need_human_guess" && made >= 1;

  let kick = actingTeam.toUpperCase();
  let headline = "";
  let detail = "";

  if (pendingAck) {
    const ackTeam = pendingAck.team || playerTeam() || actingTeam;
    const bystander = pendingAck.label === "Civilian";
    const singleTeamBlue = view.single_team && pendingAck.label === "Blue";
    kick = "Turn over";
    headline = bystander ? "Bystander" : (singleTeamBlue ? "Blue card" : "Enemy card");
    detail = bystander
      ? "Nobody's card. Your turn is over."
      : (singleTeamBlue
          ? "That advances the Blue set. Your turn is over."
          : "That helps them. Your turn is over.");
    kicker.textContent = kick;
    kicker.classList.remove("warn");
    title.textContent = headline;
    sub.textContent = detail;
    renderHintIdeas();
    el.className = "command " + ackTeam.toLowerCase() + " your-move await-ack";
    document.body.classList.toggle("your-guess", false);
    document.body.classList.toggle("can-end", false);
    document.body.classList.toggle("await-ack", true);
    document.body.classList.toggle("urgency", racePressure() === "urgent");
    return;
  }
  document.body.classList.toggle("await-ack", false);

  if (view.status === "over") {
    kick = "Match";
    headline = "Game over";
  } else if (view.status === "need_human_guess") {
    if (canEnd && number > 0 && made >= number) {
      kick = "Bonus guess";
      headline = "One extra guess—or stop";
      detail = "You reached the clue count. The extra guess is optional.";
    } else if (canEnd) {
      kick = "Your turn";
      headline = "Keep going or stop";
      detail = racePressure() === "urgent"
        ? "They are close. Another " + actingTeam + " still helps."
        : "Another " + actingTeam + " is still good.";
    } else {
      kick = "Your turn";
      headline = racePressure() === "urgent" ? "Catch up now" : "Tap a matching card";
      detail = racePressure() === "urgent"
        ? otherTeam + " is close. Only " + actingTeam + " helps you."
        : actingTeam + " is yours. " + otherTeam + " helps them win.";
    }
  } else if (view.status === "need_human_clue") {
    kick = "Your turn";
    headline = racePressure() === "urgent" ? "You need a bigger clue" : "Write a clue";
    detail = racePressure() === "urgent"
      ? "They are close. Point at more " + actingTeam + "."
      : "Point at " + actingTeam + ". " + otherTeam + " helps them win.";
  } else if (view.status === "ai_turn") {
    kick = actingTeam + " · " + actingRole;
    headline = view.phase === "clue" ? "Writing a clue" : "Picking a card";
    detail = "Waiting.";
  } else {
    kick = actingTeam;
    headline = actingRole || "Standby";
  }

  if (lastHint && lastHint.ideas && lastHint.ideas.length) {
    kick = "Might be wrong";
    detail = "";
  }
  kicker.textContent = kick;
  kicker.classList.toggle("warn", Boolean(lastHint));
  title.textContent = headline;
  sub.textContent = detail;
  renderHintIdeas();
  el.className =
    "command " +
    (actingTeam === "Red" ? "red" : "blue") +
    (actingHuman ? " your-move" : " wait") +
    (canEnd ? " can-end" : "") +
    (canEnd && number > 0 && made >= number ? " bonus-ready" : "") +
    (lastHint && lastHint.ideas && lastHint.ideas.length ? " has-hint" : "");
  document.body.classList.toggle("your-guess", view.status === "need_human_guess");
  document.body.classList.toggle("can-end", canEnd);
  document.body.classList.toggle("urgency", racePressure() === "urgent");
}

function renderHintIdeas() {
  const panel = $("hint-ideas");
  if (!panel) return;
  const ideas = !pendingAck && lastHint && lastHint.ideas ? lastHint.ideas : [];
  if (!ideas.length) {
    panel.innerHTML = "";
    panel.classList.add("hidden");
    return;
  }
  panel.innerHTML = ideas.map((idea) => (
    "<span title=\"" + escapeHtml(idea.reason || "") + "\"><b>" + escapeHtml(idea.word) + "</b><i>" + escapeHtml(idea.reason || "") + "</i></span>"
  )).join("");
  panel.classList.remove("hidden");
}

/* ---------- roster / personas ---------- */

function updateRoster() {
  const left = $("roster-left");
  const right = $("roster-right");
  const groups = view.single_team
    ? [[left, ["red_cm"]], [right, ["red_g"]]]
    : [[left, ["red_cm", "red_g"]], [right, ["blue_cm", "blue_g"]]];
  left.innerHTML = "";
  right.innerHTML = "";
  left.className = "player-rail left red-rail";
  right.className = "player-rail right " + (view.single_team ? "red-rail" : "blue-rail");
  groups.forEach(([rail, seats]) => seats.forEach((seat) => {
    const team = seatTeam(seat);
    const active = view.acting_seat === seat;
    const think = active && view.status === "ai_turn";
    const kind = seatKind(seat);
    const relationship = playerTeam() ? kindLabel(kind) : team;
    const el = document.createElement("div");
    el.className = "agent " + team.toLowerCase() + " " + kind + " " + (seatRole(seat) === "cm" ? "cm" : "op") + (active ? " active" : "") + (think ? " think" : "");
    if (active) el.setAttribute("aria-current", "step");
    el.innerHTML =
      '<span class="port ' + (seatRole(seat) === "cm" ? "hint" : "flip") + '"><img class="portrait" src="' + portraitFor(seat) + '" alt="" /></span>' +
      '<div class="who"><span class="nm">' + roleLabel(seat) + "</span>" +
      '<span class="rl">' + relationship + " · " + (seatRole(seat) === "cm" ? "writes clues" : "picks cards") + "</span></div>" +
      (active ? '<span class="now">' + (kind === "you" ? "Your turn" : "Active") + "</span>" : "");
    rail.appendChild(el);
  }));
}

/* ---------- clue bar ---------- */

let lastClueKey = "";
function updateCluebar() {
  const bar = $("cluebar");
  if (pendingAck && pendingAck.clue) {
    const tone = String(pendingAck.team || playerTeam() || "Red").toLowerCase();
    bar.className = "command-clue has-clue " + tone;
    bar.innerHTML =
      '<span class="clue-label">Clue</span>' +
      '<span class="clue-word">' + pendingAck.clue + "</span>" +
      '<span class="clue-num">' + pendingAck.number + "</span>";
    return;
  }
  const tone = turnTeam() === "Red" ? "red" : "blue";
  if (!view.pending) {
    bar.className = "command-clue " + tone;
    bar.innerHTML = '<span class="clue-label">Clue</span><span class="clue-idle">None yet</span>';
    lastClueKey = "";
    return;
  }
  const key = view.pending.clue + " " + view.pending.number;
  bar.className = "command-clue has-clue " + tone;
  bar.innerHTML =
    '<span class="clue-label">Clue</span>' +
    '<span class="clue-word">' + view.pending.clue + "</span>" +
    '<span class="clue-num">' + view.pending.number + "</span>";
  if (key !== lastClueKey) lastClueKey = key;
}

/* ---------- controls ---------- */

function updateControls() {
  const c = $("controls");
  c.innerHTML = "";
  if (view.status === "over") return;

  if (pendingAck) {
    const go = document.createElement("button");
    go.className = "end-turn primary";
    go.innerHTML = 'Continue <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h12M13 6l6 6-6 6"/></svg>';
    go.onclick = acknowledgeMiss;
    c.appendChild(go);
    return;
  }

  if (view.status === "ai_turn") {
    if (!autoplay) {
      const next = document.createElement("button");
      next.className = "primary";
      next.textContent = retryNeeded ? "TRY AGAIN" : "ADVANCE";
      next.onclick = () => {
        retryNeeded = false;
        autoplay = true;
        act(null);
      };
      c.appendChild(next);
    }
    return;
  }

  if (view.status === "need_human_clue") {
    const word = document.createElement("input");
    word.type = "text"; word.placeholder = "CODEWORD"; word.id = "clueword"; word.autocomplete = "off"; word.setAttribute("aria-label", "Clue word");
    const num = document.createElement("input");
    const ownLeft = turnTeam() === "Blue" ? view.counts.blue_left : view.counts.red_left;
    num.type = "number"; num.min = "1"; num.max = String(Math.max(1, ownLeft)); num.value = String(Math.min(2, Math.max(1, ownLeft))); num.id = "cluenum"; num.style.width = "54px"; num.setAttribute("aria-label", "Clue number");
    const go = document.createElement("button");
    go.className = "primary"; go.textContent = "Send clue";
    const submit = () => act({ type: "clue", word: word.value, number: parseInt(num.value, 10) || 1 });
    go.onclick = submit;
    word.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
    c.append(word, num, go);
    setTimeout(() => word.focus(), 30);
    return;
  }

  if (view.status === "need_human_guess") {
    const made = view.pending ? view.pending.made : 0;
    if ((view.hints_remaining || 0) > 0) {
      const hint = document.createElement("button");
      hint.className = "ghost hintbtn";
      hint.title = "Request a hint";
      hint.textContent = "Hint · " + view.hints_remaining;
      hint.onclick = askHint;
      c.appendChild(hint);
    }
    const stop = document.createElement("button");
    const ready = made >= 1;
    const bonusAvailable = view.pending && made >= view.pending.number;
    stop.className = "end-turn" + (ready ? " primary" : "") + (bonusAvailable ? " bonus-ready" : "");
    stop.innerHTML = ready
      ? 'End turn <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h12M13 6l6 6-6 6"/></svg>'
      : "End turn";
    stop.disabled = !ready;
    stop.setAttribute("aria-disabled", ready ? "false" : "true");
    stop.title = ready ? "Finish this turn and pass to the next seat" : "Guess at least one card first";
    stop.onclick = () => act({ type: "stop" });
    c.appendChild(stop);
  }
}

/* ---------- timeline ---------- */

function updateTimeline() {
  const tl = $("timeline");
  const rows = [];
  let cur = null;
  (view.history || []).forEach((m) => {
    const role = String(m[0] || "");
    if (role.endsWith("_Codemaster")) {
      cur = { team: role.startsWith("Blue") ? "blue" : "red", clue: m[1], num: m[2], guesses: [] };
      rows.push(cur);
    } else if (role.endsWith("_Guesser") && cur) {
      cur.guesses.push({ word: m[1], label: String(m[2] || "").replace(/\*/g, "") });
    }
  });
  tl.innerHTML = "";
  rows.forEach((r) => {
    const dots = r.guesses
      .map((g) => '<span class="guess-dot"><span class="pip ' + g.label.toLowerCase() + '"></span>' + g.word + "</span>")
      .join("");
    const row = document.createElement("div");
    row.className = "turn-row";
    row.innerHTML =
      '<span class="tag ' + r.team + '">' + r.team.toUpperCase() + "</span>" +
      '<div class="body"><span class="clue">' + r.clue + '</span><span class="num">' + r.num + "</span>" + dots + "</div>";
    tl.appendChild(row);
  });
  tl.scrollTop = tl.scrollHeight;
}

/* ---------- toast ---------- */

function toast(message, duration = 4500) {
  const t = $("toast");
  t.textContent = message;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), duration);
}

/* ---------- problem report ---------- */

function reportSnapshot() {
  const active = Boolean(view && !$("game").classList.contains("hidden"));
  const cells = active && Array.isArray(view.cells)
    ? view.cells.map((cell) => ({
        word: cell.word,
        revealed: Boolean(cell.revealed),
        // Strip the unrevealed key before the report leaves the browser.
        label: cell.revealed ? cell.label : null,
      }))
    : [];
  return {
    message: $("report-message").value,
    contact: $("report-contact").value,
    website: $("report-website").value,
    page: {
      url: window.location.href,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      user_agent: navigator.userAgent,
    },
    game: active ? {
      active: true,
      single_team: Boolean(view.single_team),
      status: view.status,
      turn: view.turn,
      phase: view.phase,
      acting_seat: view.acting_seat,
      winner: view.winner,
      end: view.end,
      seats: view.seats,
      counts: view.counts,
      pending: view.pending,
      red_clues: view.red_clues,
      hints_remaining: view.hints_remaining,
      history: view.history,
      cells,
    } : { active: false },
    ui: {
      pending_ack: pendingAck,
      retry_needed: retryNeeded,
      suggested_words: suggestedIndexes
        .map((index) => view && view.cells && view.cells[index] && view.cells[index].word)
        .filter(Boolean),
    },
  };
}

function resetReportDialog() {
  reportBusy = false;
  $("report-send").disabled = false;
  $("report-send").textContent = "Save log & send";
  $("report-cancel").textContent = "Cancel";
  $("report-status").textContent = "";
  $("report-status").className = "report-status";
}

function openReportDialog() {
  const dialog = $("report-dialog");
  if (!dialog || dialog.open) return;
  resetReportDialog();
  dialog.showModal();
  setTimeout(() => $("report-message").focus(), 30);
}

function closeReportDialog() {
  const dialog = $("report-dialog");
  if (dialog && dialog.open) dialog.close();
}

async function submitReport(event) {
  event.preventDefault();
  if (reportBusy) return;
  reportBusy = true;
  const send = $("report-send");
  const status = $("report-status");
  send.disabled = true;
  send.textContent = "Saving…";
  status.className = "report-status";
  status.textContent = "Saving the game log and notifying Team Edamame…";
  try {
    const response = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reportSnapshot()),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (_error) {
      data = {};
    }
    if (!response.ok || !data.ok) {
      const saved = data.saved && data.report_id
        ? " The log was saved as " + data.report_id + "."
        : "";
      throw new Error((data.error || "Could not send the report.") + saved);
    }
    status.className = "report-status success";
    status.textContent = "Sent. Report ID: " + data.report_id;
    send.textContent = "Sent";
    $("report-cancel").textContent = "Done";
  } catch (error) {
    status.className = "report-status error";
    status.textContent = String(error && error.message ? error.message : error);
    send.disabled = false;
    send.textContent = "Try again";
    reportBusy = false;
  }
}

/* ---------- overlay + confetti ---------- */

function endInfo() {
  const stored = view && view.end;
  if (stored && stored.reason) {
    return {
      reason: stored.reason,
      word: stored.word || "",
      by: stored.by || "",
    };
  }
  const history = (view && view.history) || [];
  for (let i = history.length - 1; i >= 0; i -= 1) {
    const row = history[i] || [];
    if (String(row[2] || "").includes("ASSASSIN")) {
      return {
        reason: "assassin",
        word: row[1] || "",
        by: String(row[0] || "").startsWith("Blue") ? "Blue" : "Red",
      };
    }
  }
  return {
    reason: "cleared",
    word: "",
    by: view && view.winner === "B" ? "Blue" : "Red",
  };
}

function overlayCopy() {
  const end = endInfo();
  const team = playerTeam();
  const winnerTeam = view.winner === "R" ? "Red" : "Blue";
  const word = String(end.word || "").toUpperCase();
  const named = word || "the assassin";
  const assassin = end.reason === "assassin";
  const clues = view.red_clues === 1 ? "1 clue" : view.red_clues + " clues";

  if (view.single_team) {
    if (view.winner === "R") {
      return {
        win: true,
        title: "Red team cleared the board",
        sub: "All 9 agents were found in " + clues + ".",
      };
    }
    if (!assassin) {
      return {
        win: false,
        title: "The Blue set was cleared",
        sub: "Revealing all 8 Blue cards ends a single-team game.",
      };
    }
    return {
      win: false,
      title: "You hit the assassin",
      sub: named === "the assassin"
        ? "The assassin ends a co-op game."
        : named + " was the assassin. That ends the game.",
    };
  }

  if (!team) {
    if (assassin) {
      return {
        win: true,
        title: (end.by || "A team") + " hit the assassin",
        sub: named === "the assassin"
          ? winnerTeam + " wins because the assassin was tapped."
          : named + " was the assassin. " + winnerTeam + " wins.",
      };
    }
    return {
      win: true,
      title: winnerTeam + " team wins",
      sub: winnerTeam + " found every agent first.",
    };
  }

  if (assassin && end.by === team) {
    return {
      win: false,
      title: "You hit the assassin",
      sub: named === "the assassin"
        ? "The assassin ends the game. " + winnerTeam + " wins."
        : named + " was the assassin. That ends the game. " + winnerTeam + " wins.",
    };
  }
  if (assassin) {
    return {
      win: true,
      title: "They hit the assassin",
      sub: named === "the assassin"
        ? "The assassin ends their turn and the game. You win."
        : named + " was the assassin. Their guess loses. You win.",
    };
  }
  if (winnerTeam === team) {
    return {
      win: true,
      title: winnerTeam + " wins",
      sub: end.by === team
        ? "You found every " + winnerTeam + " agent first."
        : "They finished your set. " + winnerTeam + " wins.",
    };
  }
  return {
    win: false,
    title: winnerTeam + " wins — you lose",
    sub: end.by === team
      ? "You found their last agent."
      : winnerTeam + " found every agent first.",
  };
}

function showOverlay() {
  if (endShown) return;
  endShown = true;
  const ov = $("overlay");
  const title = $("overlay-title");
  const sub = $("overlay-sub");
  const state = $("overlay-state");
  const symbol = $("overlay-symbol");
  const stats = $("overlay-stats");
  const team = playerTeam();
  const winnerTeam = view.winner === "R" ? "Red" : "Blue";
  const spectator = !team;
  const end = endInfo();
  const copy = overlayCopy();
  const win = copy.win;

  title.textContent = copy.title;
  sub.textContent = copy.sub;

  let statHtml = view.single_team
    ? '<span><strong>' + view.red_clues + '</strong><small>CLUES USED</small></span>' +
      '<span><strong>' + (9 - view.counts.red_left) + '</strong><small>RED FOUND</small></span>'
    : '<span><strong>' + (9 - view.counts.red_left) + '</strong><small>RED FOUND</small></span>' +
      '<span><strong>' + (8 - view.counts.blue_left) + '</strong><small>BLUE FOUND</small></span>';
  if (end.reason === "assassin" && end.word) {
    statHtml += '<span class="assassin-stat"><strong>' + escapeHtml(String(end.word).toUpperCase()) + '</strong><small>ASSASSIN</small></span>';
  }
  stats.innerHTML = statHtml;

  state.textContent = spectator ? "MATCH COMPLETE" : (win ? "VICTORY" : "MISSION FAILED");
  state.className = "overlay-state " + (win ? "win" : "loss");
  title.className = "overlay-title " + (end.reason === "assassin" ? "assassin" : winnerTeam.toLowerCase());
  symbol.innerHTML = win
    ? '<svg viewBox="0 0 64 64"><path d="M32 5 55 14v17c0 14-9.5 24-23 28C18.5 55 9 45 9 31V14Z"/><path d="m20 32 8 8 17-19"/></svg>'
    : '<svg viewBox="0 0 64 64"><path d="M32 5 55 14v17c0 14-9.5 24-23 28C18.5 55 9 45 9 31V14Z"/><path d="M23 23l18 18M41 23 23 41"/></svg>';
  ov.className =
    "overlay " +
    (win ? "victory" : "loss") +
    (end.reason === "assassin" ? " assassin" : " " + winnerTeam.toLowerCase() + "-winner");
  document.body.classList.toggle("end-victory", win);
  document.body.classList.toggle("end-loss", !win);
  ov.classList.remove("hidden");
  if (win) confettiBurst();
  else lossBurst();
}

let confettiGen = 0;
function stopConfetti() {
  confettiGen += 1;
  const canvas = $("confetti");
  if (!canvas) return;
  canvas.classList.remove("on");
  const ctx = canvas.getContext("2d");
  if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
}
function hitConfetti() {
  const canvas = $("confetti"), ctx = canvas.getContext("2d");
  if (!canvas || !ctx) return;
  const gen = ++confettiGen;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = window.innerWidth * dpr;
  canvas.height = window.innerHeight * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  canvas.classList.add("on");
  const colors = ["#ff453a", "#ffd60a", "#ff8a84", "#fff4d6", "#ffffff"];
  const cx = window.innerWidth / 2;
  const cy = window.innerHeight * 0.42;
  const parts = Array.from({ length: 96 }, () => ({
    x: cx + (Math.random() - 0.5) * 90,
    y: cy,
    vx: (Math.random() - 0.5) * 14,
    vy: Math.random() * -10 - 3,
    g: 0.32 + Math.random() * 0.2,
    s: 3 + Math.random() * 5,
    c: colors[(Math.random() * colors.length) | 0],
    rot: Math.random() * Math.PI,
    vr: (Math.random() - 0.5) * 0.4,
  }));
  let f = 0;
  (function tick() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    parts.forEach((p) => {
      p.vy += p.g;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.c;
      ctx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * 0.55);
      ctx.restore();
    });
    if (++f < 150 && gen === confettiGen) requestAnimationFrame(tick);
    else if (gen === confettiGen) {
      canvas.classList.remove("on");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  })();
}

function confettiBurst() {
  const canvas = $("confetti"), ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = window.innerWidth * dpr; canvas.height = window.innerHeight * dpr; ctx.scale(dpr, dpr);
  canvas.classList.add("on");
  const colors = ["#0a84ff", "#64d2ff", "#ff453a", "#30d158", "#f5f5f7"];
  const parts = Array.from({ length: 170 }, () => ({
    x: window.innerWidth / 2 + (Math.random() - 0.5) * 220, y: window.innerHeight / 3,
    vx: (Math.random() - 0.5) * 11, vy: Math.random() * -13 - 4, g: 0.35 + Math.random() * 0.22,
    s: 4 + Math.random() * 6, c: colors[(Math.random() * colors.length) | 0], rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3,
  }));
  let f = 0;
  (function tick() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    parts.forEach((p) => { p.vy += p.g; p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillStyle = p.c; ctx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * 0.6); ctx.restore(); });
    if (++f < 170) requestAnimationFrame(tick);
    else { canvas.classList.remove("on"); ctx.clearRect(0, 0, canvas.width, canvas.height); }
  })();
}

function lossBurst() {
  const canvas = $("confetti"), ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = window.innerWidth * dpr;
  canvas.height = window.innerHeight * dpr;
  ctx.scale(dpr, dpr);
  canvas.classList.add("on");
  const colors = ["#3a3a3c", "#8e8e93", "#ff453a", "#636366"];
  const parts = Array.from({ length: 95 }, () => ({
    x: Math.random() * window.innerWidth,
    y: -20 - Math.random() * window.innerHeight * 0.35,
    vx: (Math.random() - 0.5) * 1.4,
    vy: 1.2 + Math.random() * 2.5,
    s: 2 + Math.random() * 5,
    c: colors[(Math.random() * colors.length) | 0],
    a: 0.35 + Math.random() * 0.5,
  }));
  let frame = 0;
  (function tick() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    parts.forEach((p) => {
      p.x += p.vx;
      p.y += p.vy;
      ctx.globalAlpha = p.a;
      ctx.fillStyle = p.c;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.s, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.globalAlpha = 1;
    if (++frame < 190) requestAnimationFrame(tick);
    else {
      canvas.classList.remove("on");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  })();
}

/* ---------- render + actions ---------- */

function render() {
  const words = (view.cells || []).map((c) => c.word);
  if (words.length !== boardWords.length || words.some((w, i) => w !== boardWords[i])) buildBoard(view.cells);
  const team = playerTeam();
  const keyVisible = (view.cells || []).some((cell) => !cell.revealed && cell.label);
  const turn = turnTeam();
  document.body.classList.toggle("player-red", team === "Red");
  document.body.classList.toggle("player-blue", team === "Blue");
  document.body.classList.toggle("turn-red", turn === "Red");
  document.body.classList.toggle("turn-blue", turn === "Blue");
  document.body.classList.toggle("spectator-mode", !team);
  document.body.classList.toggle("spymaster-view", view.status === "need_human_clue");
  document.body.classList.toggle("key-visible", keyVisible);
  document.body.classList.toggle("urgency", racePressure() === "urgent");
  updateHud();
  updateCommand();
  updateRoster();
  updateCluebar();
  updateBoard();
  updateControls();
  updateTimeline();
  if (view.status === "over") showOverlay();
}

function flashError(msg) {
  const c = $("controls");
  const e = document.createElement("span");
  e.className = "err"; e.textContent = msg;
  c.appendChild(e);
  c.classList.remove("shake"); void c.offsetWidth; c.classList.add("shake");
}

async function askHint() {
  if (busy || !token) return;
  busy = true;
  const started = Date.now();
  setAIThinking(true, "hint");
  try {
    const data = await api({ op: "hint", token });
    await holdThinking(started);
    if (data.token) token = data.token;
    if (data.view) view = data.view;
    if (data.hint && data.hint.ideas && data.hint.ideas.length) {
      lastHint = data.hint;
      suggestedIndexes = data.hint.ideas.map((idea) => idea.index);
    }
    render();
    if (data.error) toast(data.error);
  } catch (e) {
    await holdThinking(started);
    flashError(String(e));
  } finally {
    busy = false;
    setAIThinking(false);
  }
}

async function act(move) {
  if (busy || !token) return;
  busy = true;
  const isAITurn = !move && view && view.status === "ai_turn";
  const priorActingTeam = view && view.acting_seat ? seatTeam(view.acting_seat) : playerTeam();
  const started = Date.now();
  if (isAITurn) setAIThinking(true, "turn");
  lastHint = null;
  suggestedIndexes = [];
  const priorClue = view && view.pending ? view.pending : null;
  try {
    const data = await api({ op: "act", token, move: move || undefined });
    if (isAITurn) await holdThinking(started);
    if (data.token) token = data.token;
    if (data.error && data.view && data.view.status === "ai_turn") {
      retryNeeded = true;
      autoplay = false;
    } else if (!data.error) {
      retryNeeded = false;
    }
    if (data.view) {
      const miss = move && move.type === "guess" ? humanMissFromEvents(data.view.events) : null;
      if (miss && data.view.status !== "over") {
        pendingAck = {
          label: miss.label,
          team: priorActingTeam,
          clue: priorClue && priorClue.clue,
          number: priorClue && priorClue.number,
        };
        autoplay = false;
      }
      view = data.view;
      render();
    }
    if (data.error) flashError(data.error);
  } catch (e) {
    if (isAITurn) await holdThinking(started);
    if (isAITurn) {
      retryNeeded = true;
      autoplay = false;
      updateControls();
    }
    flashError(String(e));
  } finally {
    busy = false;
    if (isAITurn) setAIThinking(false);
    if (view && view.status === "ai_turn" && autoplay && !pendingAck) {
      setTimeout(() => act(null), 400);
    }
  }
}

function showSetupError(message) {
  const el = $("setup-error");
  if (!el) return;
  el.textContent = message;
  el.classList.remove("hidden");
}

async function start(config) {
  if (busy) return;
  busy = true;
  const setupError = $("setup-error");
  if (setupError) {
    setupError.textContent = "";
    setupError.classList.add("hidden");
  }
  try {
    const data = await api({ op: "new", single_team: config.single_team, seats: config.seats });
    if (!data || data.error || !data.token || !data.view) {
      showSetupError((data && data.error) || "Could not start a game");
      return;
    }
    $("overlay").classList.add("hidden");
    $("toast").classList.add("hidden");
    document.body.classList.remove("end-victory", "end-loss");
    document.body.classList.add("game-active");
    boardWords = []; lastHint = null; suggestedIndexes = []; pendingAck = null; retryNeeded = false; endShown = false;
    lastFound = { red: 0, blue: 0 }; hudReady = false; scoreFlash = null;
    token = data.token; view = data.view;
    $("setup").classList.add("hidden");
    $("game").classList.remove("hidden");
    render();
    if (view.status === "ai_turn" && autoplay) setTimeout(() => act(null), 500);
  } catch (err) {
    showSetupError(String(err && err.message ? err.message : err));
  } finally {
    busy = false;
  }
}

/* ---------- wiring ---------- */

document.querySelectorAll(".mode-card").forEach((btn) => {
  btn.addEventListener("click", () => { autoplay = true; start(PRESETS[btn.dataset.preset]); });
});

const customSeatIds = ["red_cm", "red_g", "blue_cm", "blue_g"];
function syncCustomSetup(changedSeat) {
  const singleTeam = $("mode").value === "single";
  if (changedSeat && changedSeat.value === "human") {
    customSeatIds.forEach((id) => {
      if ($(id) !== changedSeat) $(id).value = "ai";
    });
  }
  ["blue_cm", "blue_g"].forEach((id) => {
    if (singleTeam) $(id).value = "ai";
    $(id).disabled = singleTeam;
  });
  $("blue-seats").classList.toggle("unavailable", singleTeam);
  $("blue-seats").setAttribute("aria-disabled", singleTeam ? "true" : "false");
}

$("advanced-toggle").addEventListener("click", () => {
  const hidden = $("advanced").classList.toggle("hidden");
  $("advanced-toggle").setAttribute("aria-expanded", hidden ? "false" : "true");
});
$("start").addEventListener("click", () => {
  autoplay = true;
  start({
    single_team: $("mode").value === "single",
    seats: { red_cm: $("red_cm").value, red_g: $("red_g").value, blue_cm: $("blue_cm").value, blue_g: $("blue_g").value },
  });
});
$("mode").addEventListener("change", () => syncCustomSetup(null));
customSeatIds.forEach((id) => $(id).addEventListener("change", (event) => syncCustomSetup(event.target)));
syncCustomSetup(null);

$("report-open").addEventListener("click", openReportDialog);
$("report-close").addEventListener("click", closeReportDialog);
$("report-cancel").addEventListener("click", closeReportDialog);
$("report-form").addEventListener("submit", submitReport);
$("report-dialog").addEventListener("close", () => {
  $("report-form").reset();
  resetReportDialog();
});

function backToSetup() {
  endShown = false;
  pendingAck = null;
  hudReady = false;
  lastFound = { red: 0, blue: 0 };
  scoreFlash = null;
  document.body.classList.remove("game-active", "end-victory", "end-loss", "player-red", "player-blue", "turn-red", "turn-blue", "spectator-mode", "spymaster-view", "key-visible", "your-guess", "ai-busy", "guess-hit", "guess-miss", "guess-kill", "guess-enemy", "urgency", "await-ack");
  $("overlay").classList.add("hidden");
  $("game").classList.add("hidden");
  $("setup").classList.remove("hidden");
}
$("newgame").addEventListener("click", backToSetup);
$("overlay-again").addEventListener("click", backToSetup);
