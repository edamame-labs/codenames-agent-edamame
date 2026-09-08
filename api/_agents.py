"""Load the frozen Team Edamame submission agents, then wrap them for ClueCast.

`submission/` is treated as the single source of truth and is never edited. We
synthesize the tiny ``players`` base-class package the agents import (so the
framework checkout is not needed at runtime) and inject the OpenRouter shim as
the ``client``. Clue generation, listener simulation, and optional hints remain
separate calls; listener-facing calls never receive the key.
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from pydantic import BaseModel, Field

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SUBMISSION = os.path.join(_ROOT, "submission")

DEFAULT_MODEL = "openai/gpt-5.6-terra"
DEFAULT_SIMULATOR_MODEL = "openai/gpt-5.6-luna"
DEFAULT_HINT_MODEL = "openai/gpt-5.6-luna"
CODEMASTER_AUTO_ATTEMPTS = 3
CODEMASTER_GENERATOR_TIMEOUT = 20.0
CODEMASTER_SIMULATOR_TIMEOUT = 12.0


class AgentTurnError(RuntimeError):
    """An AI turn failed without changing game state."""


class WebClueResponse(BaseModel):
    clue: str
    targets: list[str] = Field(min_length=1, max_length=9)

    @property
    def number(self) -> int:
        return len(self.targets)


class ListenerSimulationResponse(BaseModel):
    strategy: str = Field(min_length=1, max_length=180)
    ranking: list[str] = Field(min_length=1, max_length=10)
    planned_guesses: int = Field(ge=1, le=10)


class HintIdeaModel(BaseModel):
    word: str
    why: str = Field(min_length=1, max_length=72)


class SimpleHintResponse(BaseModel):
    ideas: list[HintIdeaModel] = Field(min_length=2, max_length=3)


def _install_players_package() -> None:
    """Provide the minimal ``players.codemaster`` / ``players.guesser`` classes."""
    if "players" in sys.modules:
        return

    class Codemaster(ABC):
        def __init__(self):
            self.move_history = []

        def set_move_history(self, move_history):
            self.move_history = move_history

        def get_move_history(self):
            return self.move_history

        @abstractmethod
        def set_game_state(self, words_on_board, key_grid):
            ...

        @abstractmethod
        def get_clue(self):
            ...

    class Guesser(ABC):
        def __init__(self):
            self.move_history = []

        def set_move_history(self, move_history):
            self.move_history = move_history

        def get_move_history(self):
            return self.move_history

        @abstractmethod
        def set_board(self, words_on_board):
            ...

        @abstractmethod
        def set_clue(self, clue, num_guesses):
            ...

        @abstractmethod
        def keep_guessing(self):
            ...

        @abstractmethod
        def get_answer(self):
            ...

    players = types.ModuleType("players")
    players.__path__ = []  # mark as a package
    codemaster_mod = types.ModuleType("players.codemaster")
    guesser_mod = types.ModuleType("players.guesser")
    codemaster_mod.Codemaster = Codemaster
    guesser_mod.Guesser = Guesser
    sys.modules["players"] = players
    sys.modules["players.codemaster"] = codemaster_mod
    sys.modules["players.guesser"] = guesser_mod


_install_players_package()
if _SUBMISSION not in sys.path:
    sys.path.insert(0, _SUBMISSION)

import codemaster_team as codemaster_module  # noqa: E402
from codemaster_team import TeamCodemaster, is_legal_clue  # noqa: E402
from guesser_team import TeamGuesser  # noqa: E402
from _openrouter import OpenRouterClient  # noqa: E402


def legal_human_clue(word: str, board: list, used: list) -> bool:
    return is_legal_clue(word, board, used)


def model_slug() -> str:
    # Project-scoped (not the generic OPENROUTER_MODEL) so laptop defaults are
    # never picked up. Configure CLUECAST_OPENROUTER_MODEL in Vercel.
    return os.environ.get("CLUECAST_OPENROUTER_MODEL") or DEFAULT_MODEL


def hint_model_slug() -> str:
    # Keep hints independently configurable while defaulting to Luna in code.
    return os.environ.get("CLUECAST_HINT_MODEL") or DEFAULT_HINT_MODEL


def simulator_model_slug() -> str:
    return os.environ.get("CLUECAST_SIMULATOR_MODEL") or DEFAULT_SIMULATOR_MODEL


class WebCodemaster(TeamCodemaster):
    """Clue generation with two independent key-blind listener simulations."""

    def __init__(self, team: str, **kwargs):
        super().__init__(team, **kwargs)
        self.single_team = False
        self.simulator_model = simulator_model_slug()
        self.api_timeout = CODEMASTER_GENERATOR_TIMEOUT
        self.simulator_timeout = CODEMASTER_SIMULATOR_TIMEOUT
        self.auto_attempts = CODEMASTER_AUTO_ATTEMPTS
        self.last_listener_plans = []
        self.retry_feedback = []

    def set_single_team(self, enabled: bool) -> None:
        self.single_team = bool(enabled)

    def _allowed_numbers(self, own_count: int):
        return list(range(1, max(1, int(own_count)) + 1))

    def _scoreboard(self, groups):
        own = len(groups.get("own") or [])
        opponent = len(groups.get("opponent") or [])
        if self.single_team or opponent == 0:
            race = "single"
        elif own > opponent:
            race = "behind"
        elif own < opponent:
            race = "ahead"
        else:
            race = "tied"
        deficit = 0 if race == "single" else max(0, own - opponent)
        far_behind = race == "behind" and deficit >= 2
        late_behind = race == "behind" and 0 < opponent <= 3
        if own >= 3 and (far_behind or late_behind):
            preferred = 3
        elif own >= 2 and race in {"behind", "tied", "single"}:
            preferred = 2
        else:
            preferred = 1
        return {
            "own_remaining": own,
            "opponent_remaining": opponent,
            "civilian_remaining": len(groups.get("civilian") or []),
            "track": "single_team" if race == "single" else "two_team",
            "race_state": race,
            "deficit": deficit,
            "far_behind": far_behind,
            "late_behind": late_behind,
            "minimum_preferred_number": min(preferred, max(1, own)),
        }

    def _prompt_payload(self, pairs):
        groups = self._groups(pairs)
        own_count = len(groups["own"])
        history = list(self.get_move_history() or [])
        scoreboard = self._scoreboard(groups)
        state = {
            "team": self.team.upper(),
            "track": scoreboard["track"],
            "board_by_label": groups,
            "allowed_native_numbers": self._allowed_numbers(own_count),
            "public_history": self._history(),
            "used_clues": self._used_clues(),
            "unfinished_previous_clues": codemaster_module.unfinished_public_clues(
                history,
                self.team,
            ),
            "scoreboard": scoreboard,
            "strategy": {
                "objective": (
                    "clear all own cards in as few clues as possible without "
                    "risking the maximum loss score"
                    if scoreboard["race_state"] == "single"
                    else (
                        "you are too far behind; take the largest coherent group and "
                        "maximize win probability, not per-clue safety"
                        if scoreboard["far_behind"] or scoreboard["late_behind"]
                        else (
                            "win the race; maximize win probability, not per-clue safety"
                            if scoreboard["race_state"] == "behind"
                            else "maximize expected own cards cleared by this clue"
                        )
                    )
                ),
                "number_one_policy": (
                    "a clue of 1, or a timid 2, usually loses when you are behind "
                    "by 2+ or the opponent is near the finish; use 1 only if every "
                    "larger group would make the assassin a first guess"
                    if scoreboard["far_behind"] or scoreboard["late_behind"]
                    else (
                        "a clue of 1 is usually a losing move when behind; use it only "
                        "if every larger group would make the assassin a first guess"
                        if scoreboard["race_state"] == "behind"
                        else "use 1 only when no coherent group of 2 or more exists"
                    )
                ),
            },
            "rejected_attempts": list(self.retry_feedback[-1:]),
        }
        instructions = (
            "{}\n\n"
            "You are the Codemaster. You see the key. Your teammate does not.\n"
            "scoreboard.race_state is binding.\n"
            "- behind: the opponent is closer to winning. Prefer the largest "
            "coherent own-card group (usually 2 or 3). A clue of 1 is a last "
            "resort used only when every larger set would make the assassin a "
            "first guess. Civilian risk is acceptable. Do not make an opponent "
            "card the first guess.\n"
            "- far behind (deficit >= 2) or late behind (opponent has 3 or fewer "
            "cards): be more aggressive. Prefer 3 when a coherent trio exists. A "
            "timid 1 or 2 usually loses the race. Civilian risk is acceptable. "
            "Still never make the assassin a plausible first guess.\n"
            "- tied: still prefer 2+; do not stall with a timid 1 if a clean "
            "pair exists.\n"
            "- ahead: you may play cleaner, but still take a 2 when both "
            "targets are ordinary associations.\n"
            "- single: fewer clues is better; take the largest clean group.\n"
            "Never make the assassin a plausible first guess. Separate "
            "key-blind listeners will test the candidate, so do not simulate "
            "guesses yourself. Never repeat an entry in rejected_attempts. "
            "Return exactly two fields: clue and targets. Do not return a number "
            "field; the clue number is derived from targets.length. The number of "
            "targets must be in allowed_native_numbers, and every target must be "
            "copied exactly from the own-card list."
        ).format(codemaster_module.GAME_RULES)
        if self.retry_feedback:
            instructions += (
                "\n\nFORMAT REPAIR REQUIRED: The previous response failed parsing "
                "or validation. Return only one JSON object shaped exactly like "
                '{"clue":"WORD","targets":["BOARD_WORD"]}. Do not add markdown, '
                "commentary, number, listener fields, or extra keys."
            )
        return instructions, json.dumps(
            state,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
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
        if client is None:
            return None, "missing_client"
        try:
            response = client.responses.parse(
                model=model,
                instructions=instructions,
                input=payload,
                text_format=WebClueResponse,
                reasoning={"effort": reasoning},
                max_output_tokens=800,
                store=False,
                timeout=timeout,
            )
        except Exception:
            return None, "generator_api_error"
        parsed = getattr(response, "output_parsed", None)
        if parsed is not None:
            return parsed, None
        parse_error = str(
            getattr(response, "parse_error", None) or "unknown"
        )[:160]
        return None, "generator_malformed_response[{}]".format(parse_error)

    def _simulate_one(self, selection, active, profile):
        client = self._get_client()
        if client is None:
            return None, "simulator_missing_client"
        history = list(self.get_move_history() or [])
        max_actions = min(len(active), int(selection.number) + 1)
        payload = {
            "listener_profile": profile,
            "clue": selection.clue,
            "number": selection.number,
            "live_words": active,
            "public_history": self._history(),
            "unfinished_previous_clues": codemaster_module.unfinished_public_clues(
                history,
                self.team,
            ),
            "maximum_actions": max_actions,
        }
        instructions = (
            "You are one independent, key-blind Codenames listener. You never "
            "see card colors, ownership, intended targets, or the hidden key. "
            "Using only ordinary meanings, the clue, and public history, rank up "
            "to maximum_actions distinct live words, including alternatives you "
            "would consider after the announced number. planned_guesses is how "
            "many ranked words you would actually guess if every prior guess "
            "were reported correct. Follow the announced number when associations "
            "are plausible; do not stop early merely to be conservative. Copy "
            "words exactly from live_words. strategy is one short public summary "
            "of how you choose and stop, not private chain-of-thought."
        )
        try:
            response = client.responses.parse(
                model=self.simulator_model,
                instructions=instructions,
                input=json.dumps(payload, separators=(",", ":"), sort_keys=True),
                text_format=ListenerSimulationResponse,
                reasoning={"effort": "none"},
                max_output_tokens=320,
                store=False,
                timeout=self.simulator_timeout,
            )
        except Exception:
            return None, "simulator_api_error"
        parsed = getattr(response, "output_parsed", None)
        ranking = [
            codemaster_module.normalize_word(word)
            for word in list(getattr(parsed, "ranking", None) or [])
        ]
        ranking = list(dict.fromkeys(word for word in ranking if word in active))[
            :max_actions
        ]
        planned_guesses = min(
            len(ranking),
            max(1, int(getattr(parsed, "planned_guesses", 1) or 1)),
        )
        actions = [
            {
                "word": word,
                "continue_if_correct": index + 1 < planned_guesses,
            }
            for index, word in enumerate(ranking[:planned_guesses])
        ]
        if (
            parsed is None
            or not str(getattr(parsed, "strategy", "")).strip()
            or not ranking
            or not actions
        ):
            return None, "simulator_malformed_response"
        return {
            "profile": profile,
            "strategy": str(parsed.strategy).strip()[:180],
            "ranking": ranking,
            "actions": actions,
        }, None

    def _simulate_listener(self, selection, groups):
        active = sorted(
            str(word).strip().upper()
            for words in groups.values()
            for word in words
            if str(word).strip()
        )
        profiles = (
            "literal player; follow the announced count for plausible matches",
            "broad associative player; explore multiple natural matches",
        )
        pool = ThreadPoolExecutor(max_workers=2)
        futures = [
            pool.submit(self._simulate_one, selection, active, profile)
            for profile in profiles
        ]
        plans = []
        try:
            for index, future in enumerate(futures, start=1):
                try:
                    plan, reason = future.result(
                        timeout=self.simulator_timeout + 2.0
                    )
                except FutureTimeout:
                    plan, reason = None, "simulator_timeout"
                except Exception:
                    plan, reason = None, "simulator_internal_error"
                if plan is None:
                    return None, "{}_listener_{}".format(
                        reason or "simulator_failed",
                        index,
                    )
                plans.append(plan)
        finally:
            pool.shutdown(wait=False)
        return plans, None

    def _gate(self, selection, raw, groups):
        del raw
        plans, reason = self._simulate_listener(selection, groups)
        if not plans:
            return None, reason or "simulator_failed"
        self.last_listener_plans = plans
        own = {
            codemaster_module.normalize_word(word)
            for word in groups.get("own") or []
        }
        assassin = {
            codemaster_module.normalize_word(word)
            for word in groups.get("assassin") or []
        }
        assassin_cap = int(selection.number)
        for listener_index, plan in enumerate(plans, start=1):
            for action_index, action in enumerate(plan["actions"], start=1):
                if action["word"] in assassin:
                    if action_index == 1:
                        return None, "listener_{}_assassin_first".format(
                            listener_index
                        )
                    assassin_cap = min(assassin_cap, action_index - 1)
                    break

        race = self._scoreboard(groups)["race_state"]
        supported_number = 0
        for action_index in range(int(selection.number)):
            ranked_words = [
                plan["ranking"][action_index]
                for plan in plans
                if action_index < len(plan["ranking"])
            ]
            if not ranked_words:
                break
            safe_votes = sum(
                word in own for word in ranked_words
            )
            if safe_votes == 0 and (action_index == 0 or race != "behind"):
                break
            supported_number = action_index + 1
        if supported_number == 0:
            return None, "simulators_consensus_non_own_first"
        final_number = min(supported_number, assassin_cap)
        if final_number < selection.number:
            return (
                codemaster_module.Selection(
                    selection.clue,
                    final_number,
                    tuple(selection.targets[:final_number]),
                ),
                "simulators_clamped_before_risk",
            )
        return selection, None

    def get_clue(self):
        pairs = self._pairs()
        groups = self._groups(pairs)
        own = groups["own"]
        active = [word for word, _label in pairs]
        used = self._used_clues()
        if not own:
            raise AgentTurnError("no own cards remain")

        last_reason = "unknown"
        started = time.monotonic()
        for attempt in range(1, self.auto_attempts + 1):
            raw = None
            try:
                selection, reason, raw = self._model_clue(
                    pairs,
                    groups,
                    own,
                    active,
                    used,
                    model=self.model,
                    reasoning=self.reasoning_effort,
                    timeout=self.api_timeout,
                )
            except Exception:
                selection, reason = None, "internal_error"
            last_reason = reason or "unknown"
            candidate_number = getattr(raw, "number", None)
            final_number = selection.number if selection is not None else None
            self.harness_events.append(
                {
                    "phase": "web_codemaster_attempt",
                    "attempt": attempt,
                    "reason": reason,
                    "candidate_number": candidate_number,
                    "final_number": final_number,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
            if selection is not None:
                print(
                    "cluecast_ai phase=codemaster outcome=success attempt={} "
                    "candidate_number={} final_number={} gate={}".format(
                        attempt,
                        candidate_number,
                        final_number,
                        reason or "accepted",
                    ),
                    flush=True,
                )
                return selection.clue, max(1, int(selection.number))
            if raw is not None:
                self.retry_feedback.append(
                    {
                        "clue": str(getattr(raw, "clue", ""))[:32],
                        "number": candidate_number,
                        "rejection": last_reason,
                    }
                )
            elif last_reason.startswith("generator_malformed_response"):
                self.retry_feedback.append(
                    {
                        "rejection": last_reason,
                        "required_fields": ["clue", "targets"],
                        "number_source": "targets.length",
                    }
                )
            print(
                "cluecast_ai phase=codemaster outcome=retry attempt={} "
                "candidate_number={} reason={}".format(
                    attempt,
                    candidate_number,
                    last_reason,
                ),
                flush=True,
            )

        raise AgentTurnError(
            "safe clue generation failed after {} attempts ({})".format(
                self.auto_attempts,
                last_reason,
            )
        )


def codemaster(team: str) -> WebCodemaster:
    slug = model_slug()
    agent = WebCodemaster(team, client=OpenRouterClient(), model=slug)
    agent.backup_model = ""
    agent.reasoning_effort = "none"
    return agent


def guesser(team: str) -> TeamGuesser:
    slug = model_slug()
    agent = TeamGuesser(team, client=OpenRouterClient(), model=slug)
    agent.backup_model = ""  # single model call per turn
    agent.reasoning_effort = "none"
    return agent


def hint_guesser(team: str) -> TeamGuesser:
    """Return the key-blind operative used only for player hints."""
    agent = TeamGuesser(
        team,
        client=OpenRouterClient(),
        model=hint_model_slug(),
    )
    agent.backup_model = ""
    agent.reasoning_effort = "none"
    return agent


def simple_hint(team: str, board: list, clue: str, number: int):
    """Return a few key-blind ideas, never treated as the correct answers."""
    active = [str(word).strip().upper() for word in board if not str(word).startswith("*")]
    if not active:
        return None
    payload = {
        "team": team,
        "clue": str(clue).strip().upper(),
        "number": int(number),
        "live_words": active,
    }
    instructions = (
        "You are a deliberately simple Codenames helper. You never see the key "
        "or card colors. Suggest two or three different live words that might "
        "fit the clue by ordinary meaning. Use different kinds of association "
        "when you can, such as category, part, place, or scene. For each word "
        "give one short why. Do not claim they are correct. Do not provide "
        "private chain-of-thought."
    )
    try:
        response = OpenRouterClient().responses.parse(
            model=hint_model_slug(),
            instructions=instructions,
            input=json.dumps(payload),
            text_format=SimpleHintResponse,
            reasoning={"effort": "none"},
            max_output_tokens=280,
            timeout=30.0,
        )
        parsed = response.output_parsed
        ideas = []
        seen = set()
        for item in (parsed.ideas if parsed else []):
            word = str(item.word).strip().upper()
            why = str(item.why).strip()[:72]
            if word in active and word not in seen and why:
                seen.add(word)
                ideas.append((word, why))
        if len(ideas) >= 2:
            return ideas[:3]
    except Exception:
        pass

    agent = hint_guesser(team)
    agent.set_board(board)
    agent.set_clue(clue, number)
    word = agent.get_answer()
    word = str(word).strip().upper() if word else ""
    if word not in active:
        return None
    return [(word, "Simplest visible match. It might still be wrong.")]
