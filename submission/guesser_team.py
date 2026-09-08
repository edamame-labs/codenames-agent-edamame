"""Team Edamame v0 Guesser: exact-n ranking with unfinished public clues.

Public-release note: the competition credential was removed. Supply an API key
through ``OPENAI_API_KEY``; no credential is embedded in this repository.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Dict, List, Optional

from players.guesser import Guesser

try:
    from openai import OpenAI
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - isolated no-key import
    OpenAI = None
    BaseModel = object
    Field = None


REVEALED = {"*RED*", "*BLUE*", "*CIVILIAN*", "*ASSASSIN*"}
INVERSE_CLUES = {"UNRELATED", "NOTHING", "ABSENCE", "VOID", "SEMANTIC"}
GUESSER_TIMEOUT = 45.0
BACKUP_TIMEOUT = 15.0
# Hard wall-clock stop for the first get_answer of a clue, enforced by us rather
# than by the SDK. Later guesses in the same turn reuse the cached plan.
TURN_DEADLINE = 50.0


def _api_key() -> str:
    if os.environ.get("V0_OFFLINE"):
        return ""
    return str(os.environ.get("OPENAI_API_KEY") or "").strip()


GAME_RULES = """
Codenames is played on a 25-word board. Each word is own, opponent, civilian,
or the assassin. Guess only live board words. Infer the ordinary meaning of
the clue. Use no hidden color information, board-position signal, or private
convention. A miss or the assassin ends the turn; the assassin also loses.
""".strip()

GUESSER_RULES = """
{}
You are the field operative. You do not see the key. The Codemaster may be a
stranger. Rank live board words for the current clue first. Unfinished
previous clues of your team remain among the live words after a miss or early
stop. If the announced number is 0, make exactly one guess. If the clue is a
clear inverse placeholder (UNRELATED, NOTHING, ABSENCE, VOID), pick the live
word least associated with it.
""".format(GAME_RULES).strip()

GUESSER_QUESTION = (
    "Return a ranking of exactly return_exactly_this_many_words distinct live "
    "board words, ordered from most to least likely intended guesses. Copy "
    "each word exactly."
)

if Field is not None:

    class GuesserResponse(BaseModel):
        ranking: List[str] = Field(min_length=1, max_length=25)

else:
    GuesserResponse = None


def normalize_word(value: object) -> str:
    return str(value).strip().upper()


def unfinished_public_clues(history, team, current_clue=None) -> List[Dict[str, Any]]:
    team_name = "Blue" if str(team).strip().lower() == "blue" else "Red"
    cm_role = "{}_Codemaster".format(team_name)
    guesser_role = "{}_Guesser".format(team_name)
    own_label = team_name.upper()
    current = normalize_word(current_clue) if current_clue else ""
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
    if current and turns and turns[-1]["clue"] == current:
        turns = turns[:-1]
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
                "remaining_announced": announced - team_hits,
                "guessed_words": [guess["word"] for guess in guesses],
            }
        )
    return unfinished


def validate_ranking(raw_ranking, active_words, count) -> List[str]:
    if count < 1 or not isinstance(raw_ranking, (list, tuple)):
        return []
    if len(raw_ranking) != count:
        return []
    ranking = [normalize_word(word) for word in raw_ranking]
    active = {normalize_word(word) for word in active_words}
    if len(set(ranking)) != count or any(word not in active for word in ranking):
        return []
    return ranking


class TeamGuesser(Guesser):
    """Key-blind v0 guesser. Stops at the announced number."""

    def __init__(self, team="Red", **kwargs):
        super().__init__()
        self.team = "Blue" if str(team).strip().lower() == "blue" else "Red"
        self.model = str(
            kwargs.get("model")
            or kwargs.get("version")
            or os.environ.get("EDAMAME_G_MODEL")
            or "gpt-5.6-sol"
        )
        self.reasoning_effort = str(
            kwargs.get("reasoning_effort")
            or os.environ.get("EDAMAME_G_REASONING")
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
        self.api_timeout = GUESSER_TIMEOUT
        self.backup_timeout = BACKUP_TIMEOUT
        self.turn_deadline = TURN_DEADLINE
        self._client = kwargs.get("client")
        self.words: List[str] = []
        self.clue = ""
        self.num_guesses = 1
        self.harness_events: List[Dict[str, Any]] = []
        self._plan: List[str] = []
        self._plan_ready = False
        self._plan_valid = False
        self._turn_cap = 1
        self._guessed: List[str] = []
        self._made = 0

    def set_board(self, words):
        self.words = list(words or [])

    def set_clue(self, clue, num_guesses):
        self.clue = str(clue or "").strip()
        try:
            self.num_guesses = max(0, int(num_guesses))
        except (TypeError, ValueError):
            self.num_guesses = 1
        self._plan = []
        self._plan_ready = False
        self._plan_valid = False
        self._turn_cap = 1
        self._guessed = []
        self._made = 0

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

    def _active(self) -> List[str]:
        active: List[str] = []
        for raw in self.words:
            word = str(raw).strip().upper()
            if (
                word
                and word not in REVEALED
                and not word.startswith("*")
                and word not in active
            ):
                active.append(word)
        return active

    def _requested(self, active_count: int) -> int:
        if self.num_guesses == 0:
            return min(1, active_count)
        return min(max(0, self.num_guesses), active_count)

    def _public_history(self) -> List[Dict[str, Any]]:
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

    def _payload(self, active: List[str], cap: int) -> str:
        state = {
            "clue": self.clue,
            "announced_number": self.num_guesses,
            "return_exactly_this_many_words": cap,
            "live_board_words": active,
            "unfinished_previous_clues": unfinished_public_clues(
                list(super().get_move_history() or []),
                self.team,
                current_clue=self.clue,
            ),
            "recent_public_history": self._public_history(),
            "inverse_placeholder": normalize_word(self.clue) in INVERSE_CLUES,
        }
        return json.dumps(
            state, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )

    def _rank(
        self,
        active: List[str],
        cap: int,
        *,
        model: str,
        reasoning: str,
        timeout: float,
    ) -> List[str]:
        client = self._get_client()
        if client is None or GuesserResponse is None:
            return []
        try:
            response = client.responses.parse(
                model=model,
                reasoning={"effort": reasoning},
                instructions="{}\n\n{}".format(GUESSER_RULES, GUESSER_QUESTION),
                input=self._payload(active, cap),
                text_format=GuesserResponse,
                max_output_tokens=2500 if reasoning in {"high", "xhigh", "max"} else 600,
                store=False,
                timeout=timeout,
            )
        except Exception:
            return []
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            return []
        return validate_ranking(
            list(getattr(parsed, "ranking", None) or []), active, cap
        )

    def _accept(self, ranking: List[str], cap: int, source: str) -> None:
        self._plan = list(ranking)
        self._turn_cap = cap
        self._plan_valid = True
        self._plan_ready = True
        self.harness_events.append({"phase": "guesser_turn", "source": source})

    def _settle(self, future, started: float) -> List[str]:
        """Wait for a call only until the turn deadline, then give up on it."""

        remaining = max(0.0, self.turn_deadline - (time.monotonic() - started))
        try:
            return future.result(timeout=remaining)
        except FutureTimeout:
            return []
        except Exception:
            return []

    def _build_plan(self, active: List[str]) -> None:
        cap = self._requested(len(active))
        self._turn_cap = cap
        if cap < 1:
            self._plan = []
            self._plan_valid = True
            self._plan_ready = True
            return
        ranking: List[str] = []
        relief: List[str] = []
        started = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            primary = pool.submit(
                self._rank,
                active,
                cap,
                model=self.model,
                reasoning=self.reasoning_effort,
                timeout=self.api_timeout,
            )
            backup = (
                pool.submit(
                    self._rank,
                    active,
                    1,
                    model=self.backup_model,
                    reasoning=self.backup_reasoning,
                    timeout=self.backup_timeout,
                )
                if self.backup_model
                else None
            )
            ranking = self._settle(primary, started)
            if not ranking and backup is not None:
                relief = self._settle(backup, started)
        finally:
            pool.shutdown(wait=False)
        if ranking:
            self._accept(ranking, cap, "model")
            return
        if relief:
            self._accept(relief, 1, "backup")
            return
        self._accept([active[0]], 1, "local_fallback")

    def _next_word(self) -> Optional[str]:
        live = set(self._active())
        for word in self._plan:
            if word in live and word not in self._guessed:
                return word
        return None

    def get_answer(self):
        try:
            active = self._active()
            if not active:
                return None
            if not self._plan_ready:
                self._build_plan(active)
            if self._made >= self._turn_cap:
                return None
            answer = self._next_word()
            if answer is None:
                # The cached plan is exhausted before the announced count. Stop
                # rather than emit an unplanned board word, except that the very
                # first guess of a turn must always return a live word.
                if self._made == 0:
                    return active[0]
                return None
            self._guessed.append(answer)
            self._made += 1
            return answer
        except Exception:
            active = self._active()
            return active[0] if active else None

    def keep_guessing(self):
        if not self._plan_valid or self._made >= self._turn_cap:
            return False
        return self._next_word() is not None
