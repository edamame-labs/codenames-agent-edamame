"""Team Edamame v0 Codemaster: Sol-high with a parallel Terra backup.

Public-release note: the competition credential was removed. Supply an API key
through ``OPENAI_API_KEY``; no credential is embedded in this repository.
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from players.codemaster import Codemaster

try:
    from openai import OpenAI
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - isolated no-key import
    OpenAI = None
    BaseModel = object
    Field = None


REVEALED = {"*RED*", "*BLUE*", "*CIVILIAN*", "*ASSASSIN*"}
ASCII_WORD = re.compile(r"^[A-Za-z]+$")
IRREGULAR_ROOTS = {
    "axis": "axis",
    "axes": "axis",
    "child": "child",
    "children": "child",
    "foot": "foot",
    "feet": "foot",
    "goose": "goose",
    "geese": "goose",
    "knife": "knife",
    "knives": "knife",
    "man": "man",
    "men": "man",
    "mouse": "mouse",
    "mice": "mouse",
    "person": "person",
    "people": "person",
    "tooth": "tooth",
    "teeth": "tooth",
    "woman": "woman",
    "women": "woman",
}
EMERGENCY_CLUES = ("UNRELATED", "NOTHING", "ABSENCE", "VOID", "SEMANTIC")
PRIMARY_TIMEOUT = 45.0
BACKUP_TIMEOUT = 15.0
# Hard wall-clock stop for one get_clue call, enforced by us rather than by the
# SDK. The organizer soft limit is 60s; a late model must not be able to spend it.
TURN_DEADLINE = 50.0


def _api_key() -> str:
    if os.environ.get("V0_OFFLINE"):
        return ""
    return str(os.environ.get("OPENAI_API_KEY") or "").strip()


GAME_RULES = """
Codenames is played on a 25-word board. Each word is own, opponent, civilian,
or the assassin. A turn is one clue from the Codemaster and one or more guesses
from the field operative. A correct own-card guess may continue. An opponent
card, civilian, or the assassin ends the turn; the assassin also loses the game.
Clues are one unhyphenated English word plus a number. The number is how many
own cards the clue is meant to cover. Do not announce 0: in this competition
that allows unlimited guesses. The clue must not be a live board word, a
substring of one, or an inflection or derivation of one. Do not use spelling
tricks, letter counts, board position, punctuation, or a private codebook.
""".strip()

CODEMASTER_RULES = """
{}
You are the Codemaster. You see the key. Your teammate does not and may be a
stranger. Protect the assassin first, then opponent cards, then civilians.
Choose an ordinary association a new teammate would recognize. Return only the
structured fields asked for. Copy every target exactly from the own-card list.
""".format(GAME_RULES).strip()

V0_RULES = """
Official Single-Team score is the number of clues; fewer clues is better.
Prefer a clean pair over isolated singles only when both targets independently
would be a stranger's top-two associations. Do not glue a leftover onto a
unique lock. If the assassin is a plausible first or second association,
discard the clue or announce 1. The first guess must be an own word a stranger
would pick first. If a previous clue was missed or stopped early, leftover
associations remain on the live board. After a miss, change the semantic
family. Never announce 0. Fill listener_first and listener_second as a
key-blind stranger's first two guesses among live board words, including the
assassin if that is what they would try. listener_second is the stranger's
second guess, not your intended second target; if they differ or the second
association is a stretch, announce 1.
""".strip()

V0_QUESTION = (
    "Return one legal clue, its number, and the exact own-card targets. Number "
    "must be in allowed_native_numbers. Also return listener_first and "
    "listener_second: the live board words a key-blind stranger would guess "
    "first and second. If you are not confident a stranger would take both "
    "intended targets in order, announce 1."
)

if Field is not None:

    class V0CodemasterResponse(BaseModel):
        clue: str
        number: int = Field(ge=1, le=2)
        targets: List[str] = Field(min_length=1, max_length=2)
        listener_first: str
        listener_second: str

else:
    V0CodemasterResponse = None


class Selection(NamedTuple):
    clue: str
    number: int
    targets: Tuple[str, ...]


def normalize_word(value: object) -> str:
    return str(value).strip().upper()


def _label(value: object) -> str:
    return str(value).strip().strip("*").upper()


def rough_stem(word: object) -> str:
    value = re.sub(r"[^a-z]", "", str(word).lower())
    if len(value) > 5 and value.endswith("ies"):
        return value[:-3] + "y"
    for suffix in (
        "ments",
        "ment",
        "ingly",
        "edly",
        "ing",
        "ers",
        "er",
        "ed",
        "es",
        "s",
    ):
        if len(value) - len(suffix) >= 4 and value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def is_legal_clue(clue: object, active_words, used_clues=()) -> bool:
    if not isinstance(clue, str):
        return False
    candidate = clue.strip()
    if not 2 <= len(candidate) <= 32 or ASCII_WORD.fullmatch(candidate) is None:
        return False
    lowered = candidate.lower()
    clue_stem = rough_stem(candidate)
    clue_irregular = IRREGULAR_ROOTS.get(lowered)
    for board_word in active_words:
        board = re.sub(r"[^a-z]", "", str(board_word).lower())
        if not board:
            continue
        if lowered in board or board in lowered:
            return False
        if clue_irregular is not None and IRREGULAR_ROOTS.get(board) == clue_irregular:
            return False
        if len(clue_stem) >= 4 and clue_stem == rough_stem(board):
            return False
    return normalize_word(candidate) not in {
        normalize_word(word) for word in used_clues if word
    }


def unfinished_public_clues(history, team) -> List[Dict[str, Any]]:
    team_name = "Blue" if str(team).strip().lower() == "blue" else "Red"
    cm_role = "{}_Codemaster".format(team_name)
    guesser_role = "{}_Guesser".format(team_name)
    own_label = team_name.upper()
    turns: List[Dict[str, Any]] = []
    active: Optional[Dict[str, Any]] = None
    for move in history or ():
        if not move:
            continue
        role = str(move[0])
        if role == cm_role:
            if active is not None:
                turns.append(active)
            announced = 0
            if len(move) > 2:
                try:
                    announced = int(move[2])
                except (TypeError, ValueError):
                    announced = 0
            active = {
                "clue": normalize_word(move[1]),
                "announced_number": announced,
                "guesses": [],
            }
        elif role == guesser_role and active is not None:
            reveal = str(move[2]).strip().strip("*").upper() if len(move) > 2 else ""
            active["guesses"].append(
                {"word": normalize_word(move[1]), "own": reveal == own_label}
            )
    if active is not None:
        turns.append(active)
    unfinished: List[Dict[str, Any]] = []
    for turn in turns:
        announced = max(0, int(turn["announced_number"]))
        if announced <= 0:
            continue
        guesses = list(turn["guesses"])
        team_hits = sum(1 for guess in guesses if guess["own"])
        if team_hits >= announced:
            continue
        unfinished.append(
            {
                "clue": turn["clue"],
                "announced_number": announced,
                "team_hits": team_hits,
                "remaining_announced": announced - team_hits,
                "guessed_words": [guess["word"] for guess in guesses],
            }
        )
    return unfinished


def validate_selection(
    raw: object,
    *,
    own_words: Sequence[str],
    active_words: Sequence[str],
    allowed_numbers: Sequence[int],
    used_clues: Sequence[str] = (),
) -> Optional[Selection]:
    clue_raw = getattr(raw, "clue", None)
    number_raw = getattr(raw, "number", None)
    targets_raw = getattr(raw, "targets", None)
    if type(number_raw) is not int or not isinstance(targets_raw, list):
        return None
    if number_raw not in set(allowed_numbers) or len(targets_raw) != number_raw:
        return None
    own = {normalize_word(word) for word in own_words}
    targets: List[str] = []
    for value in targets_raw:
        if not isinstance(value, str):
            return None
        target = normalize_word(value)
        if target not in own or target in targets:
            return None
        targets.append(target)
    if not is_legal_clue(clue_raw, active_words, used_clues):
        return None
    return Selection(normalize_word(clue_raw), number_raw, tuple(targets))


# Ordinary, mutually distinct placeholder words tried after the fixed emergency
# pool, followed by consonant-only synthetic tokens that cannot be a substring,
# superstring, inflection, or stem of an ordinary board word. This guarantees a
# board-legal fallback clue even if every earlier candidate is already used or
# blocked by the live board.
_EXTENDED_FALLBACK_CLUES = (
    "MATTER",
    "OBJECT",
    "NOTION",
    "CONCEPT",
    "SUBJECT",
    "TOPIC",
    "THEME",
    "SYMBOL",
    "PATTERN",
    "SIGNAL",
    "ELEMENT",
    "FACTOR",
    "REGION",
    "BALANCE",
    "MIXTURE",
    "SEGMENT",
    "QUALITY",
    "VECTOR",
    "MARGIN",
)
_FALLBACK_CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ"


def _legal_fallback_candidates():
    for word in _EXTENDED_FALLBACK_CLUES:
        yield word
    for length in range(3, 9):
        for start in range(len(_FALLBACK_CONSONANTS)):
            yield "".join(
                _FALLBACK_CONSONANTS[(start + offset) % len(_FALLBACK_CONSONANTS)]
                for offset in range(length)
            )


class TeamCodemaster(Codemaster):
    """Sol-high v0 Codemaster with a same-turn Terra-none backup."""

    def __init__(self, team="Red", **kwargs):
        super().__init__()
        self.team = "Blue" if str(team).strip().lower() == "blue" else "Red"
        self.model = str(
            kwargs.get("model")
            or kwargs.get("version")
            or os.environ.get("EDAMAME_CM_MODEL")
            or "gpt-5.6-sol"
        )
        self.reasoning_effort = str(
            kwargs.get("reasoning_effort")
            or os.environ.get("EDAMAME_CM_REASONING")
            or "high"
        )
        self.backup_model = str(
            kwargs.get("backup_model")
            or os.environ.get("V0_BACKUP_MODEL")
            or "gpt-5.6-terra"
        )
        self.backup_reasoning = str(
            kwargs.get("backup_reasoning")
            or os.environ.get("V0_BACKUP_REASONING")
            or "none"
        )
        self.api_timeout = PRIMARY_TIMEOUT
        self.backup_timeout = BACKUP_TIMEOUT
        self.turn_deadline = TURN_DEADLINE
        self._client = kwargs.get("client")
        self.words: List[str] = []
        self.key_grid: List[str] = []
        self.harness_events: List[Dict[str, Any]] = []

    def set_game_state(self, words_on_board, key_grid):
        self.words = list(words_on_board or [])
        self.key_grid = list(key_grid or [])

    def _get_client(self):
        if self._client is not None:
            return self._client
        key = _api_key()
        if OpenAI is None or not key:
            return None
        try:
            self._client = OpenAI(
                api_key=key, timeout=self.api_timeout, max_retries=0
            )
        except Exception:
            return None
        return self._client

    def _pairs(self) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = []
        for raw_word, raw_label in zip(self.words, self.key_grid):
            word = str(raw_word).strip().upper()
            if word and word not in REVEALED and not word.startswith("*"):
                pairs.append((word, _label(raw_label)))
        return pairs

    def _groups(self, pairs: Sequence[Tuple[str, str]]) -> Dict[str, List[str]]:
        own = self.team.upper()
        enemy = "BLUE" if own == "RED" else "RED"
        return {
            "own": [word for word, label in pairs if label == own],
            "opponent": [word for word, label in pairs if label == enemy],
            "civilian": [word for word, label in pairs if label == "CIVILIAN"],
            "assassin": [word for word, label in pairs if label == "ASSASSIN"],
        }

    def _track(self) -> str:
        history = list(super().get_move_history() or [])
        red = sum(bool(move) and move[0] == "Red_Codemaster" for move in history)
        blue = sum(bool(move) and move[0] == "Blue_Codemaster" for move in history)
        if self.team == "Blue" or blue:
            return "two_team"
        if red:
            return "single_team"
        return "unknown"

    def _used_clues(self) -> List[str]:
        return [
            str(move[1]).strip().upper()
            for move in list(super().get_move_history() or [])
            if isinstance(move, (list, tuple))
            and len(move) >= 2
            and str(move[0]).endswith("_Codemaster")
        ]

    def _history(self) -> List[Dict[str, Any]]:
        public: List[Dict[str, Any]] = []
        for move in list(super().get_move_history() or [])[-12:]:
            if not isinstance(move, (list, tuple)) or len(move) < 3:
                continue
            role = str(move[0])
            if role.endswith("_Codemaster"):
                try:
                    number = int(move[2])
                except (TypeError, ValueError):
                    continue
                public.append({"role": role, "clue": str(move[1]), "number": number})
            elif role.endswith("_Guesser"):
                public.append(
                    {"role": role, "guess": str(move[1]), "outcome": str(move[2])}
                )
        return public

    def _allowed_numbers(self, own_count: int) -> List[int]:
        return [number for number in (1, 2) if number <= own_count]

    def _fallback(
        self, own: Sequence[str], active: Sequence[str], used: Sequence[str]
    ) -> Selection:
        del own
        for clue in EMERGENCY_CLUES:
            if is_legal_clue(clue, active, used):
                return Selection(clue, 1, ())
        # Guarantee a board-legal clue even when every fixed placeholder is
        # already used this game or blocked by a live board word.
        for candidate in _legal_fallback_candidates():
            if is_legal_clue(candidate, active, used):
                return Selection(candidate, 1, ())
        return Selection("SEMANTIC", 1, ())

    def _prompt_payload(
        self, pairs: Sequence[Tuple[str, str]]
    ) -> Tuple[str, str]:
        groups = self._groups(pairs)
        own_count = len(groups["own"])
        state = {
            "team": self.team.upper(),
            "track": self._track(),
            "board_by_label": groups,
            "allowed_native_numbers": self._allowed_numbers(own_count),
            "public_history": self._history(),
            "used_clues": self._used_clues(),
            "unfinished_previous_clues": unfinished_public_clues(
                list(super().get_move_history() or []),
                self.team,
            ),
            "strategy": {
                "own_remaining": own_count,
                "aim_turns": 0 if own_count == 0 else max(1, (own_count + 1) // 2),
            },
        }
        instructions = "{}\n\n{}\n\n{}".format(
            CODEMASTER_RULES, V0_RULES, V0_QUESTION
        )
        return instructions, json.dumps(
            state, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )

    def _complete(
        self,
        instructions: str,
        payload: str,
        *,
        model: str,
        reasoning: str,
        timeout: float,
    ):
        client = self._get_client()
        if client is None or V0CodemasterResponse is None:
            return None, "missing_client"
        try:
            response = client.responses.parse(
                model=model,
                reasoning={"effort": reasoning},
                instructions=instructions,
                input=payload,
                text_format=V0CodemasterResponse,
                max_output_tokens=2500 if reasoning in {"high", "xhigh", "max"} else 600,
                store=False,
                timeout=timeout,
            )
        except Exception:
            return None, "api_error"
        return getattr(response, "output_parsed", None), None

    def _gate(
        self, selection: Selection, raw: object, groups: Dict[str, List[str]]
    ):
        own = {normalize_word(word) for word in groups.get("own") or []}
        assassin = {normalize_word(word) for word in groups.get("assassin") or []}
        first = normalize_word(getattr(raw, "listener_first", None) or "")
        second = normalize_word(getattr(raw, "listener_second", None) or "")
        # A forecast guess is safe only when it is a live own card. An assassin,
        # opponent, or civilian word, an off-board word, and a missing forecast
        # are all treated as unsafe, so a malformed forecast can never smuggle a
        # pair (or an unverified single) past the gate.
        if first not in own:
            return None, "v0_assassin_first" if first in assassin else "v0_non_own_first"
        if selection.number <= 1:
            return selection, None
        if second not in own:
            return Selection(selection.clue, 1, (first,)), "v0_clamped"
        return selection, None

    def _model_clue(
        self,
        pairs,
        groups,
        own,
        active,
        used,
        *,
        model: Optional[str] = None,
        reasoning: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        instructions, payload = self._prompt_payload(pairs)
        raw, reason = self._complete(
            instructions,
            payload,
            model=model or self.model,
            reasoning=reasoning or self.reasoning_effort,
            timeout=self.api_timeout if timeout is None else timeout,
        )
        selection = (
            validate_selection(
                raw,
                own_words=own,
                active_words=active,
                allowed_numbers=self._allowed_numbers(len(own)),
                used_clues=used,
            )
            if raw is not None
            else None
        )
        if selection is None:
            return None, reason or "malformed_response", raw
        selection, gate_reason = self._gate(selection, raw, groups)
        if selection is None:
            return None, gate_reason or "v0_rejected", raw
        return selection, gate_reason, raw

    def _settle(self, future, started: float):
        """Wait for a call only until the turn deadline, then give up on it."""

        remaining = max(0.0, self.turn_deadline - (time.monotonic() - started))
        try:
            return future.result(timeout=remaining)
        except FutureTimeout:
            return None, "turn_deadline", None
        except Exception:
            return None, "internal_error", None

    def get_clue(self):
        pairs = self._pairs()
        groups = self._groups(pairs)
        own = groups["own"]
        active = [word for word, _ in pairs]
        used = self._used_clues()
        try:
            if not own:
                selection = self._fallback(own, active, used)
                return selection.clue, selection.number
            selection = None
            reason = None
            backup_selection = None
            backup_reason = None
            started = time.monotonic()
            pool = ThreadPoolExecutor(max_workers=2)
            try:
                primary = pool.submit(
                    self._model_clue, pairs, groups, own, active, used
                )
                backup = (
                    pool.submit(
                        self._model_clue,
                        pairs,
                        groups,
                        own,
                        active,
                        used,
                        model=self.backup_model,
                        reasoning=self.backup_reasoning,
                        timeout=self.backup_timeout,
                    )
                    if self.backup_model
                    else None
                )
                selection, reason, _raw = self._settle(primary, started)
                if selection is None and backup is not None:
                    backup_selection, backup_reason, _raw = self._settle(
                        backup, started
                    )
            finally:
                pool.shutdown(wait=False)
            if selection is None and backup_selection is not None:
                selection = backup_selection
                reason = "v0_backup_after_{}".format(reason or "api_error")
            if selection is None:
                selection = self._fallback(own, active, used)
                reason = reason or backup_reason or "local_fallback"
            self.harness_events.append(
                {
                    "phase": "codemaster_turn",
                    "reason": reason,
                    "announced_number": selection.number,
                }
            )
            return selection.clue, max(1, int(selection.number))
        except Exception:
            selection = self._fallback(own, active, used)
            return selection.clue, 1
