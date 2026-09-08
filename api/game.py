"""Vercel Python function: create and advance a ClueCast game.

POST body is one of:
  {"op": "new", "single_team": bool, "seats": {...}, "seed": int?}
  {"op": "act", "token": "<opaque>", "move": {...}?}

Responses always include a fresh encrypted ``token`` plus a client-safe ``view``.
The hidden board key never leaves the server except where a spymaster/spectator
view legitimately shows it.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _engine
import _state

_SEATS = ("red_cm", "red_g", "blue_cm", "blue_g")
_SEAT_TYPES = {"ai", "human"}


def _new_game_config(payload: dict) -> tuple:
    single_team = payload.get("single_team", True)
    if not isinstance(single_team, bool):
        return None, "single_team must be a boolean"

    raw_seats = payload.get("seats") or {}
    if not isinstance(raw_seats, dict):
        return None, "seats must be an object"
    invalid = [
        seat
        for seat in _SEATS
        if seat in raw_seats and raw_seats.get(seat) not in _SEAT_TYPES
    ]
    if invalid:
        return None, "each seat must be ai or human"

    seats = {
        seat: ("human" if raw_seats.get(seat) == "human" else "ai")
        for seat in _SEATS
    }
    humans = [seat for seat in _SEATS if seats[seat] == "human"]
    if len(humans) > 1:
        return None, "choose at most one human seat"
    if single_team and any(seat.startswith("blue_") for seat in humans):
        return None, "Blue seats are not used in a single-team game"
    return (single_team, seats), None


def _handle(payload: dict) -> tuple:
    op = payload.get("op")
    if op == "new":
        config, error = _new_game_config(payload)
        if error:
            return 400, {"error": error}
        single_team, seats = config
        state = _engine.new_game(
            single_team=single_team,
            seats=seats,
            seed=payload.get("seed"),
        )
        return 200, {"token": _state.encode(state), "view": _engine.view(state, [])}

    if op == "act":
        token = payload.get("token")
        if not token:
            return 400, {"error": "missing token"}
        try:
            state = _state.decode(str(token))
        except ValueError as exc:
            return 400, {"error": str(exc)}
        move = payload.get("move")
        if move is not None and not isinstance(move, dict):
            return 400, {"error": "move must be an object"}
        state, events, error = _engine.advance(state, move)
        response = {"token": _state.encode(state), "view": _engine.view(state, events)}
        if error:
            response["error"] = error
        return 200, response

    if op == "hint":
        token = payload.get("token")
        if not token:
            return 400, {"error": "missing token"}
        try:
            state = _state.decode(str(token))
        except ValueError as exc:
            return 400, {"error": str(exc)}
        suggestion, error = _engine.hint(state)
        response = {"token": _state.encode(state), "view": _engine.view(state, [])}
        if suggestion:
            response["hint"] = suggestion
        if error:
            response["error"] = error
        return 200, response

    return 400, {"error": "unknown op"}


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # simple health check
        self._send(200, {"ok": True})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                self._send(400, {"error": "body must be a JSON object"})
                return
            code, body = _handle(payload)
            self._send(code, body)
        except Exception:  # never leak provider or stack details to the client
            self._send(500, {"error": "internal error"})
