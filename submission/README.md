# Drop-in submission files

These are the two standalone Python players from Team **Edamame's
CoG 2026 Codenames AI Competition** entry, with one publication-only
change: the embedded competition credential has been removed.

## Install

1. Clone the official
   [`stepmat/Codenames_GPT`](https://github.com/stepmat/Codenames_GPT)
   framework.
2. Copy `codemaster_team.py` and `guesser_team.py` into
   `Codenames_GPT/codenames/players/`.
3. Install the included dependencies with
   `python -m pip install -r requirements.txt`.
4. Export `OPENAI_API_KEY` in the process environment.

Do not modify the other organizer-owned framework files.

## Classes

- `players.codemaster_team.TeamCodemaster`
- `players.guesser_team.TeamGuesser`

## Example

From `Codenames_GPT/codenames/`:

```bash
export OPENAI_API_KEY="..."
python run_game.py \
  players.codemaster_team.TeamCodemaster \
  players.guesser_team.TeamGuesser \
  players.codemaster_team.TeamCodemaster \
  players.guesser_team.TeamGuesser \
  --seed 3442 --no_log
```

Add `--single_team True` for the Single-Team track.

## Publication delta

The competition delivery funded its own external calls by carrying a service
credential in the two player files. That secret is intentionally absent here.
Credential lookup now uses `OPENAI_API_KEY` only. Prompts, model placement,
validation, listener gating, exact-n behavior, timeout values, fallback
behavior, and public class interfaces are otherwise preserved.

## License

MIT. See the included `LICENSE` and `THIRD_PARTY_NOTICES.md`.
