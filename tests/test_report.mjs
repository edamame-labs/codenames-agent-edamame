import assert from "node:assert/strict";
import test from "node:test";

import { sanitizeReport } from "../api/report.js";


test("report sanitizer strips unrevealed key and unknown fields", () => {
  const report = sanitizeReport({
    message: "Card did not advance.",
    token: "must-not-survive",
    game: {
      active: true,
      single_team: false,
      status: "need_human_guess",
      turn: "Blue",
      cells: [
        { word: "MOON", revealed: false, label: "Assassin" },
        { word: "STAR", revealed: true, label: "Blue" },
      ],
      secret: "must-not-survive",
    },
  });

  assert.equal(report.game.cells[0].label, null);
  assert.equal(report.game.cells[1].label, "Blue");
  assert.equal(report.game.secret, undefined);
  assert.equal(report.token, undefined);
});


test("report sanitizer limits logs and redacts pasted credentials", () => {
  const history = Array.from({ length: 100 }, (_, index) => [
    "Red_Guesser",
    `WORD${index}`,
    "*Red*",
    true,
    "extra",
    "dropped",
  ]);
  const report = sanitizeReport({
    message: "key re_1234567890abcdefghijkl and Bearer abcdefghijklmnopqrstuvwxyz",
    contact: "player@example.com",
    game: { active: true, single_team: true, history },
  });

  assert.equal(report.game.history.length, 80);
  assert.equal(report.game.history[0][1], "WORD20");
  assert.equal(report.game.history[0].length, 5);
  assert.equal(report.contact, "player@example.com");
  assert.doesNotMatch(report.message, /re_1234567890abcdefghijkl/);
  assert.doesNotMatch(report.message, /abcdefghijklmnopqrstuvwxyz/);
});


test("report sanitizer rejects malformed contact addresses", () => {
  const report = sanitizeReport({
    message: "Something broke.",
    contact: "not-an-email",
    game: { active: false },
  });

  assert.equal(report.contact, null);
  assert.equal(report.game.active, false);
});
