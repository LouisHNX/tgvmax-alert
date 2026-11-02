# tgvmax-alert

Telegram alert when a MAX JEUNE (TGVmax) seat opens on a route you watch. Runs as a
Railway cron (every 6 h): it starts, reads your bot commands, checks the routes, sends
one message with the new seats, and exits.

Commands wait in Telegram until the next run (at most 6 h), then get a confirmation.

## 1. Telegram bot
1. In Telegram, talk to @BotFather, `/newbot`, copy the token.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `message.chat.id`. That is your chat ID.

## 2. Environment variables
| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token from BotFather |
| `ALLOWED_CHAT_ID` | the only chat whose commands are accepted (alerts go there too) |
| `DB_PATH` | SQLite file, default `./alert.db`; use `/data/alert.db` on Railway |

## 3. Local run
```
pip install -r requirements.txt
python -m unittest discover -s tests -t .        # tests
python run.py --dry-run                           # prints instead of sending, touches nothing
TELEGRAM_BOT_TOKEN=... ALLOWED_CHAT_ID=... python run.py
```

## 4. Railway
1. Push this folder to a GitHub repo, then New Project > Deploy from GitHub repo.
2. Service > Settings: add a **Volume** mounted at `/data` (the SQLite state lives there).
3. Variables: `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_ID`, `DB_PATH=/data/alert.db`.
4. `railway.json` already sets start command `python run.py`, cron `0 */6 * * *` (UTC: 00/06/12/18h)
   and restart policy Never. Check they show in Settings; no public networking needed.
5. Trigger a run by hand (Run now) and read the logs: they say which source answered.
   Expect exit code 0 in well under a minute.

If a cron service cannot mount a volume, the state would need another store (e.g. a GitHub Gist).

## 5. Things to verify once (nothing here could be checked offline)
Search for `TODO(verify)` in `sources/`.

**Planner endpoint** (`sources/planner.py`): open
https://www.maxjeune-tgvinoui.sncf/sncf-connect/max-planner, DevTools > Network > Fetch/XHR,
run a search. Compare the request with `PLANNER_URL`, `HEADERS`, `build_body()` and the response
with `parse_response()` (key names in the `PROPOSAL_*_KEYS` constants). Save a real response over
`tests/fixtures/planner_sample.json` and make the test pass.

**Station codes** (`stations.py`): the `origin`/`destination` values in that same request body are
the planner codes. You can bypass the table: `/add FRPLY FRLYS 15/11` uses raw codes (planner only,
the open-data fallback needs the short names). Note `annecy` and `nancy` currently share `FRNCY`.

**Open data** (`sources/opendata.py`): on https://data.sncf.com/explore/dataset/tgvmax/ check the
field names (`date`, `train_no`, `origine`, `destination`, `heure_depart`, `heure_arrivee`,
`od_happy_card`), the station labels (e.g. `PARIS (intramuros)`) and the `where` syntax, using the
"API" tab. Replace `tests/fixtures/opendata_sample.json` with a real record sample.

## Commands
- `/add <from> <to> <date> [HH:MM-HH:MM]`: date is `15/11`, `15/11-20/11` or `2026-11-15`
  (no year = next occurrence). Example: `/add paris lyon 15/11-20/11 08:00-12:00`
- `/list`: routes with their IDs
- `/del <id>`: remove a route
- `/now`: send all currently available seats, not only the new ones
- `/help`

A seat that disappears and comes back alerts again. Routes are deleted once their last date has passed.
For an immediate check, use Run now in Railway. Known short station names are in `stations.py`.
