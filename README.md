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
4. In Settings, set start command `python run.py` and cron `0 */6 * * *` (UTC: 00/06/12/18h).
   Railway stops reading `railway.json` on 2026-12-01. No public networking needed.
5. Trigger a run by hand (Run now) and read the logs: they say which source answered.
   Expect exit code 0 in well under a minute.

## 5. What the sources rely on (checked 2026-11-02)
**Planner** (`sources/planner.py`): `GET .../search-freeplaces-proposals?origin=&destination=&departureDateTime=`,
one call per station pair and per day, over the next 30 days. It answers per station, so `paris` and
`lyon` are resolved to real pairs through the open data. The fixture is a real browser capture.

**Blocked from Railway:** the planner answers HTTP 403 there (seen 2026-11-02). The site uses DataDome and
the page sends `x-client-*` headers that this code does not send. The open data takes over, and the logs show it.

**Open data** (`sources/opendata.py`): refreshed about once a day, so it misses seats freed during the
day. Raw codes (`/add FRPLY FRLPD 15/11`) work on the planner only.

## Commands
- `/add <from> <to> <date> [HH:MM-HH:MM]`: date is `15/11`, `15/11-20/11` or `2026-11-15`
  (no year = next occurrence). Example: `/add paris lyon 15/11-20/11 08:00-12:00`
- A weekday instead of a date repeats every week, without expiring: `/add paris lyon jeudi 08:00-12:00`
- `/list`: routes with their IDs
- `/del <id>`: remove a route
- `/now`: send all currently available seats, not only the new ones
- `/help`

A seat that disappears and comes back alerts again. Routes are deleted once their last date has passed.
For an immediate check, use Run now in Railway. Known short station names are in `stations.py`.
