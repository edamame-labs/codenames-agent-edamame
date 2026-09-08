# Evaluation guide

The release has two evaluation layers:

1. **Offline invariant tests** exercise the exact public player files with fake
   structured responses.
2. **Official-framework games** load those files against the organizer's
   unmodified `Game` implementation.

Neither layer contains an API credential. Live games require the user to export
one.

## Offline invariant suite

```bash
uv run python -m unittest discover -s tests -v
```

Coverage includes:

- interface-compatible Codemaster and Guesser base classes;
- environment-only credential lookup;
- legal nonzero offline fallback;
- rejection of malformed, illegal, and out-of-range clue proposals;
- acceptance of a clean two-word listener path;
- rejection of a dangerous first forecast;
- truncation when only the second forecast is dangerous;
- truncation when the second forecast is missing or off-board;
- invalid intended-target rejection;
- primary-error recovery through the concurrent backup;
- callback return at the hard wall-clock deadline;
- local fallback when both calls hang;
- a legal local fallback when the emergency pool is exhausted;
- exact-n Guesser stopping without `n+1`;
- defensive handling of a foreign `0`;
- one-word Guesser backup;
- unfinished-clue history;
- the published 45/15/50-second timeout constants;
- repository-wide likely-secret scanning;
- public runner metrics and response-limit detection;
- integrity of the preserved historical aggregate.

The suite requires `uv sync`, but it does not call a provider or measure
language quality. The real organizer interfaces are exercised only by the
official-framework command below.

## Official framework setup

```bash
git clone https://github.com/stepmat/Codenames_GPT.git
uv sync
```

The runner accepts either the checkout root or its `codenames/` directory:

```bash
uv run python evaluation/run_official.py \
  --framework ./Codenames_GPT \
  --mode single-self \
  --seed 3442
```

It imports the official `Game` class, dynamically loads the public submission
files, records public callback latency, and emits JSON Lines. It does not copy
players into the framework or log prompts, model responses, credentials, or
hidden board keys.

## Credentials

For live inference:

```bash
export OPENAI_API_KEY="..."
```

Alternatively, copy `.env.example` to the gitignored `.env` and populate it.
An already-exported environment value takes precedence.

For a network-free framework wiring test:

```bash
uv run python evaluation/run_official.py \
  --framework ./Codenames_GPT \
  --mode single-self \
  --seed 3442 \
  --offline
```

Offline fallback is a contract test, not a strength benchmark.

## Four competition modes

### Single-Team self

The submitted Codemaster and Guesser are paired on Red. Official score is Red
Codemaster clue count on a win and 25 on a loss.

```bash
uv run python evaluation/run_official.py \
  --framework ./Codenames_GPT \
  --mode single-self \
  --seed 3442
```

### Single-Team cross

Run both mixed directions: candidate Codemaster with a foreign Guesser, then a
foreign Codemaster with the candidate Guesser.

```bash
uv run python evaluation/run_official.py \
  --framework ./Codenames_GPT \
  --mode single-cross \
  --seed 3442 \
  --extra-pythonpath ../foreign-agent \
  --foreign-codemaster foreign_players.codemaster:ForeignCodemaster \
  --foreign-guesser foreign_players.guesser:ForeignGuesser
```

The foreign classes must implement the organizer interfaces. Keep the two
pairing results separate as well as reporting their aggregate.

### Two-Team self

The submitted pair occupies Red and Blue. This tests color symmetry and game
wiring; Red win on one board is not a strength metric.

```bash
uv run python evaluation/run_official.py \
  --framework ./Codenames_GPT \
  --mode two-self \
  --seed 3442
```

### Two-Team versus

The candidate occupies Red and an explicit opponent occupies Blue.

```bash
uv run python evaluation/run_official.py \
  --framework ./Codenames_GPT \
  --mode two-vs \
  --seed 3442 \
  --extra-pythonpath ../opponent-agent \
  --opponent-codemaster opponent.codemaster:OpponentCodemaster \
  --opponent-guesser opponent.guesser:OpponentGuesser
```

For a balanced comparison, repeat with independently generated boards and
consider a separate color-swapped protocol.

## Multiple seeds and output

Repeat `--seed` and optionally save JSONL under the ignored
`evaluation/runs/` directory:

```bash
uv run python evaluation/run_official.py \
  --framework ./Codenames_GPT \
  --mode single-self \
  --seed 3442 \
  --seed 3443 \
  --seed 3444 \
  --output evaluation/runs/single-self.jsonl
```

Relative `--output` paths are resolved from the directory where you launch the
command, not the framework checkout the runner switches into.

The command exits with status 2 if any measured `get_clue` or `get_answer`
callback exceeds 60 seconds.

Summarize one or more JSON/JSONL outputs without mixing track metrics:

```bash
uv run python evaluation/summarize.py \
  evaluation/results/v0-smoke.json
```

## Metrics

Do not combine the tracks into one numeric average:

- Single-Team: mean official score; lower is better.
- Two-Team: color-balanced win rate; higher is better.
- Safety: assassin rate and first-hazard distribution.
- Semantics: own-card yield and intended-target coverage.
- Reliability: fallback rate and malformed-output rate.
- Operations: callback p50/p95/max, response-limit breaches, cost per game.

The runner and summarizer emit the official score, win flag, assassin count,
and callback latency directly; the remaining metrics above are analysis
suggestions, not fields the tooling computes for you.

Use board-level clustering when mixed-partner evaluation creates multiple rows
from the same board.

## Preserved historical smoke evidence

`evaluation/results/v0-smoke.json` contains sanitized aggregate evidence
from a four-mode development run:

- Single-Team self: win in 6 clues; worst callback 21.3 seconds.
- Single-Team foreign teammate: win in 9 clues; worst callback 13.9 seconds.
- Two-Team self: Red win; worst callback 31.7 seconds.
- Two-Team versus: Red win; worst callback 45.2 seconds.
- Zero assassin selections and zero callbacks beyond 60 seconds.
- Three primary timeouts in the versus game were covered by ready backups.

One game per mode demonstrates wiring, fallback execution, and timing headroom.
It is not a statistically meaningful estimate and is not the official private
tournament result.

## Recommended ablations

For research claims, evaluate the following on identical boards and partners:

- no listener forecast;
- forecast fields present but no gate;
- same-call forecast versus a separately key-blind listener;
- reject-only versus reject-or-truncate;
- exact-n versus optional `n+1`;
- primary only versus primary plus backup;
- 45/15/50 timing policy versus looser provider-controlled timeouts.
