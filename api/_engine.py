"""A compact, demo-grade Codenames engine that drives the ClueCast agents.

State is a plain dict (serialized into an encrypted token between requests).
``advance`` performs one atomic step: an AI clue, a single AI guess, or a
single human action. An AI guesser plan is built once per clue, then applied
one card at a time so both teams play at the same cadence.
"""

from __future__ import annotations

import random
from typing import Optional

import _agents

NUM_RED = 9
NUM_BLUE = 8
NUM_CIVILIAN = 7
NUM_ASSASSIN = 1
BOARD_SIZE = NUM_RED + NUM_BLUE + NUM_CIVILIAN + NUM_ASSASSIN  # 25

MARKER = {
    "Red": "*RED*",
    "Blue": "*BLUE*",
    "Civilian": "*CIVILIAN*",
    "Assassin": "*ASSASSIN*",
}
_SEATS = ("red_cm", "red_g", "blue_cm", "blue_g")


def _other(team: str) -> str:
    return "Blue" if team == "Red" else "Red"


def new_game(single_team: bool, seats: dict, seed: Optional[int] = None) -> dict:
    from _words import WORDS

    if seed is None:
        seed = random.randrange(1, 2_000_000_000)
    rng = random.Random(seed)
    words = [w.upper() for w in rng.sample(WORDS, BOARD_SIZE)]
    key = (
        ["Red"] * NUM_RED
        + ["Blue"] * NUM_BLUE
        + ["Civilian"] * NUM_CIVILIAN
        + ["Assassin"] * NUM_ASSASSIN
    )
    rng.shuffle(key)
    clean_seats = {seat: ("human" if seats.get(seat) == "human" else "ai") for seat in _SEATS}
    return {
        "v": 1,
        "single_team": bool(single_team),
        "seats": clean_seats,
        "words": words,
        "key": key,
        "revealed": [False] * BOARD_SIZE,
        "turn": "Red",
        "phase": "clue",
        "history": [],
        "pending": None,
        "winner": None,
        "end": None,
        "seed": seed,
        "hints_remaining": 3,
    }


def board_words(state: dict) -> list:
    words, key, revealed = state["words"], state["key"], state["revealed"]
    return [MARKER[key[i]] if revealed[i] else words[i] for i in range(len(words))]


def _acting_seat(state: dict) -> str:
    role = "cm" if state["phase"] == "clue" else "g"
    return "{}_{}".format(state["turn"].lower(), role)


def _color_left(state: dict, color: str) -> int:
    return sum(
        1
        for i in range(len(state["key"]))
        if state["key"][i] == color and not state["revealed"][i]
    )


def _winner_after_reveal(state: dict) -> Optional[str]:
    reds = sum(
        1 for i in range(len(state["key"])) if state["key"][i] == "Red" and state["revealed"][i]
    )
    blues = sum(
        1 for i in range(len(state["key"])) if state["key"][i] == "Blue" and state["revealed"][i]
    )
    if reds >= NUM_RED:
        return "R"
    if blues >= NUM_BLUE:
        return "B"
    return None


def _end_turn(state: dict) -> None:
    state["pending"] = None
    state["ai_queue"] = None
    state["phase"] = "clue"
    if not state["single_team"]:
        state["turn"] = _other(state["turn"])


def _find_unrevealed(state: dict, word: str) -> Optional[int]:
    target = str(word).strip().upper()
    for i, w in enumerate(state["words"]):
        if not state["revealed"][i] and w == target:
            return i
    return None


def _apply_clue(state: dict, clue: str, number: int) -> None:
    team = state["turn"]
    state["history"].append(["{}_Codemaster".format(team), clue, int(number)])
    state["pending"] = {"clue": clue, "number": int(number), "made": 0}
    state["ai_queue"] = None
    state["phase"] = "guess"


def _reveal(state: dict, idx: int) -> str:
    state["revealed"][idx] = True
    return state["key"][idx]


def _finish(state: dict, winner: str, reason: str, word: Optional[str] = None) -> None:
    state["winner"] = winner
    state["phase"] = "over"
    state["end"] = {
        "reason": reason,
        "word": word,
        "by": state["turn"],
    }


def _record_guess(state: dict, word: str, label: str, keep: bool) -> None:
    team = state["turn"]
    state["history"].append(
        ["{}_Guesser".format(team), word, MARKER[label], bool(keep)]
    )


def _used_clues(state: dict) -> list:
    return [
        move[1]
        for move in state.get("history") or []
        if move and str(move[0]).endswith("_Codemaster")
    ]


def _active_clue_words(state: dict) -> list:
    return list(state["words"])


def _guess_quota(pending: Optional[dict]) -> int:
    return int((pending or {}).get("number") or 1) + 1


def _resolve_guess(state: dict, word: str, label: str, pending: dict, keep_if_own: bool) -> None:
    """Record a reveal and apply win / turn-end rules, including opponent clears."""
    team = state["turn"]
    if label == "Assassin":
        _record_guess(state, word, label, False)
        _finish(state, "B" if team == "Red" else "R", "assassin", word)
        return
    winner = _winner_after_reveal(state)
    if winner:
        _record_guess(state, word, label, False)
        _finish(state, winner, "cleared", word)
        return
    if label == team:
        pending["made"] = pending.get("made", 0) + 1
        keep = bool(keep_if_own)
        _record_guess(state, word, label, keep)
        if not keep:
            _end_turn(state)
        return
    _record_guess(state, word, label, False)
    _end_turn(state)


def _build_ai_queue(state: dict) -> list:
    pending = state["pending"] or {}
    agent = _agents.guesser(state["turn"])
    agent.set_move_history(state["history"])
    agent.set_board(board_words(state))
    quota = _guess_quota(pending)
    agent.set_clue(pending.get("clue"), quota)
    queue = []
    seen = set()
    while True:
        answer = agent.get_answer()
        if not answer:
            break
        word = str(answer).strip().upper()
        if not word or word in seen:
            break
        seen.add(word)
        queue.append(word)
        if not agent.keep_guessing():
            break
    return queue[: max(1, quota)]


def _next_ai_guess(state: dict):
    queue = list(state.get("ai_queue") or [])
    while queue:
        word = queue.pop(0)
        idx = _find_unrevealed(state, word)
        if idx is not None:
            state["ai_queue"] = queue
            return idx, state["words"][idx]
    state["ai_queue"] = []
    return None, None


def _ai_guesser_step(state: dict, events: list) -> None:
    team = state["turn"]
    pending = state["pending"] or {}
    if state.get("ai_queue") is None:
        state["ai_queue"] = _build_ai_queue(state)
    idx, word = _next_ai_guess(state)
    if idx is None:
        _end_turn(state)
        return
    label = _reveal(state, idx)
    events.append("{} guesser: {} -> {}".format(team, word, label))
    _resolve_guess(state, word, label, pending, bool(state.get("ai_queue")))


def advance(state: dict, move: Optional[dict]) -> tuple:
    """Perform one atomic step. Returns (state, events, error)."""
    events: list = []
    if state["phase"] == "over" or state["winner"]:
        return state, events, None

    team = state["turn"]
    seat = _acting_seat(state)
    seat_type = state["seats"].get(seat, "ai")

    if state["phase"] == "clue":
        if seat_type == "human":
            if not move or move.get("type") != "clue":
                return state, events, None  # await human clue
            word = str(move.get("word", "")).strip().upper()
            if not _agents.legal_human_clue(word, _active_clue_words(state), _used_clues(state)):
                return state, events, "clue must be a new word that is not on the board"
            own_left = _color_left(state, team)
            try:
                number = int(move.get("number", 1))
            except (TypeError, ValueError):
                number = 1
            number = max(1, min(number, max(1, own_left)))
            _apply_clue(state, word, number)
            events.append("{} codemaster (you): {} {}".format(team, word, number))
            return state, events, None
        # AI codemaster
        agent = _agents.codemaster(team)
        if hasattr(agent, "set_single_team"):
            agent.set_single_team(state["single_team"])
        agent.set_move_history(state["history"])
        agent.set_game_state(board_words(state), state["key"])
        try:
            clue, number = agent.get_clue()
        except _agents.AgentTurnError:
            return (
                state,
                events,
                "AI could not produce a safe clue. Please try again.",
            )
        clue = str(clue).strip().upper()
        _apply_clue(state, clue, int(number))
        events.append("{} codemaster (AI): {} {}".format(team, clue, int(number)))
        return state, events, None

    # phase == "guess"
    pending = state["pending"] or {}
    if seat_type == "human":
        if not move:
            return state, events, None  # await human guess/stop
        if move.get("type") == "stop":
            if pending.get("made", 0) < 1:
                return state, events, "you must make at least one guess"
            events.append("{} guesser (you): stop".format(team))
            _end_turn(state)
            return state, events, None
        if move.get("type") != "guess":
            return state, events, "expected a guess or stop"
        try:
            idx = int(move.get("index"))
        except (TypeError, ValueError):
            return state, events, "invalid cell"
        if idx < 0 or idx >= len(state["words"]) or state["revealed"][idx]:
            return state, events, "that cell is not selectable"
        label = _reveal(state, idx)
        word = state["words"][idx]
        events.append("{} guesser (you): {} -> {}".format(team, word, label))
        keep = pending.get("made", 0) + 1 < _guess_quota(pending)
        _resolve_guess(state, word, label, pending, keep)
        return state, events, None

    # AI guesser: one card per step, same cadence as a human operative.
    _ai_guesser_step(state, events)
    return state, events, None


def status(state: dict) -> str:
    if state["phase"] == "over" or state["winner"]:
        return "over"
    seat = _acting_seat(state)
    if state["seats"].get(seat, "ai") == "human":
        return "need_human_clue" if state["phase"] == "clue" else "need_human_guess"
    return "ai_turn"


def _has_human_guesser(state: dict) -> bool:
    guessers = ["red_g"] if state["single_team"] else ["red_g", "blue_g"]
    return any(state["seats"].get(seat) == "human" for seat in guessers)


def view(state: dict, events: Optional[list] = None) -> dict:
    st = status(state)
    show_key = (
        st in ("over", "need_human_clue")
        or (st == "ai_turn" and not _has_human_guesser(state))
    )
    cells = []
    for i, word in enumerate(state["words"]):
        revealed = state["revealed"][i]
        label = state["key"][i] if (revealed or show_key) else None
        cells.append({"word": word, "revealed": revealed, "label": label})
    red_clues = sum(
        1 for m in state["history"] if m and m[0] == "Red_Codemaster"
    )
    return {
        "status": st,
        "single_team": state["single_team"],
        "turn": state["turn"],
        "phase": state["phase"],
        "winner": state["winner"],
        "end": state.get("end"),
        "acting_seat": None if st == "over" else _acting_seat(state),
        "seats": state["seats"],
        "cells": cells,
        "counts": {
            "red_left": _color_left(state, "Red"),
            "blue_left": _color_left(state, "Blue"),
        },
        "pending": state["pending"],
        "red_clues": red_clues,
        "events": events or [],
        "history": list(state["history"]),
        "hints_remaining": state.get("hints_remaining", 0),
    }


def hint(state: dict) -> tuple:
    """Ask a separate key-blind Luna operative without revealing the board key."""
    if status(state) != "need_human_guess" or not state.get("pending"):
        return None, "hints are only available on your guess"
    if state.get("hints_remaining", 0) <= 0:
        return None, "no hints left"
    team = state["turn"]
    pairs = _agents.simple_hint(
        team,
        board_words(state),
        state["pending"]["clue"],
        state["pending"]["number"],
    )
    ideas = []
    seen = set()
    for word, reason in pairs or []:
        idx = _find_unrevealed(state, word)
        if idx is None or idx in seen:
            continue
        seen.add(idx)
        ideas.append({
            "index": idx,
            "word": state["words"][idx],
            "reason": reason,
        })
    if not ideas:
        return None, "no suggestion available"
    state["hints_remaining"] = state.get("hints_remaining", 0) - 1
    return {"ideas": ideas}, None
