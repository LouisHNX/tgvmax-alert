import unittest
from datetime import date

import db
import run
from models import SourceError, Train
from telegram import TelegramError

TODAY = date(2026, 11, 2)
CHAT = "42"


class FakeTelegram:
    def __init__(self, updates=None, fail_send=False):
        self.updates = list(updates or [])
        self.sent = []
        self.offsets = []
        self.fail_send = fail_send

    def get_updates(self, offset=0):
        self.offsets.append(offset)
        return [u for u in self.updates if u["update_id"] >= offset]

    def send_message(self, chat_id, text):
        if self.fail_send:
            raise TelegramError("boom")
        self.sent.append((str(chat_id), text))


def upd(uid, text, chat=CHAT):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


def train(no="6603", day="2026-11-15", dep="07:56", arr="09:52"):
    return Train(date=day, dep=dep, arr=arr, train_no=no, origin="Paris Gare de Lyon", dest="Lyon Part-Dieu")


class FakeSource:
    def __init__(self, trains=None, error=None):
        self.trains = trains or []
        self.error = error
        self.calls = 0

    def __call__(self, route):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.trains)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.route = db.add_route(self.conn, "paris", "lyon", "2026-11-15", "2026-11-16")

    def go(self, tg, *sources, **kw):
        return run.run(self.conn, tg, CHAT, list(sources) or [("p", FakeSource())], TODAY, **kw)

    def test_alert_once_then_silent(self):
        src = ("p", FakeSource([train()]))
        tg = FakeTelegram()
        self.assertEqual(self.go(tg, src), 0)
        self.assertEqual(len(tg.sent), 1)
        self.assertEqual(tg.sent[0][1],
                         "🚄 Paris Gare de Lyon → Lyon Part-Dieu · dimanche 15/11 · 07:56→09:52 · TGV 6603 — place MAX dispo")
        self.go(tg, src)
        self.assertEqual(len(tg.sent), 1)  # nothing new

    def test_disappear_then_return_realerts(self):
        s = FakeSource([train()])
        tg = FakeTelegram()
        self.go(tg, ("p", s))
        s.trains = []
        self.go(tg, ("p", s))
        self.assertEqual(len(tg.sent), 1)
        s.trains = [train()]
        self.go(tg, ("p", s))
        self.assertEqual(len(tg.sent), 2)

    def test_only_new_trains_listed(self):
        s = FakeSource([train("1", dep="08:00")])
        tg = FakeTelegram()
        self.go(tg, ("p", s))
        s.trains = [train("1", dep="08:00"), train("2", dep="09:00")]
        self.go(tg, ("p", s))
        self.assertIn("TGV 2", tg.sent[1][1])
        self.assertNotIn("TGV 1", tg.sent[1][1])

    def test_route_filters_applied(self):
        db.delete_route(self.conn, self.route.id)
        db.add_route(self.conn, "paris", "lyon", "2026-11-15", "2026-11-15", "08:00", "12:00")
        s = FakeSource([train("1", dep="07:00"), train("2", dep="09:00"), train("3", day="2026-11-16", dep="09:00")])
        tg = FakeTelegram()
        self.go(tg, ("p", s))
        self.assertIn("TGV 2", tg.sent[0][1])
        self.assertNotIn("TGV 1", tg.sent[0][1])
        self.assertNotIn("TGV 3", tg.sent[0][1])

    def test_grouped_by_route(self):
        db.add_route(self.conn, "lille", "nantes", "2026-11-15", "2026-11-15")
        tg = FakeTelegram()
        self.go(tg, ("p", FakeSource([train()])))
        self.assertEqual(len(tg.sent), 1)  # ONE message
        self.assertEqual(len(tg.sent[0][1].split("\n\n")), 2)

    def test_fallback_on_source_error(self):
        bad, good = FakeSource(error=SourceError("403")), FakeSource([train()])
        tg = FakeTelegram()
        db.add_route(self.conn, "lille", "nantes", "2026-11-15", "2026-11-15")
        self.go(tg, ("planner", bad), ("opendata", good))
        self.assertEqual(len(tg.sent), 1)
        self.assertEqual(bad.calls, 1)   # circuit breaker: not retried for the 2nd route
        self.assertEqual(good.calls, 2)

    def test_all_sources_fail_keeps_notified(self):
        s = FakeSource([train()])
        tg = FakeTelegram()
        self.go(tg, ("p", s))
        before = db.get_notified(self.conn, self.route.id)
        self.go(tg, ("p", FakeSource(error=SourceError("down"))))
        self.assertEqual(db.get_notified(self.conn, self.route.id), before)
        self.assertIn("⚠️", tg.sent[1][1])
        self.go(tg, ("p", s))
        self.assertEqual(len(tg.sent), 2)  # no re-alert after the outage

    def test_send_failure_keeps_state_for_retry(self):
        s = ("p", FakeSource([train()]))
        self.assertEqual(self.go(FakeTelegram(fail_send=True), s), 1)
        self.assertEqual(db.get_notified(self.conn, self.route.id), set())
        tg = FakeTelegram()
        self.assertEqual(self.go(tg, s), 0)
        self.assertEqual(len(tg.sent), 1)

    def test_commands_and_offset(self):
        tg = FakeTelegram([upd(10, "/add paris lyon 15/11"), upd(11, "/list"),
                           upd(12, "/add paris lyon 16/11", chat="999"),   # other chat: ignored
                           upd(13, "/add atlantis lyon 15/11"), upd(14, "/del 1")])
        self.go(tg)
        self.assertEqual([r.id for r in db.list_routes(self.conn)], [2])  # #1 (setUp) deleted
        replies = [t for _, t in tg.sent]
        self.assertIn("Route ajoutée", replies[0])
        self.assertIn("#2 · dimanche 15/11", replies[1])
        self.assertIn("Gare inconnue", replies[2])
        self.assertIn("supprimée", replies[3])
        self.assertEqual(len(replies), 4)
        self.assertTrue(all(c == CHAT for c, _ in tg.sent))
        self.assertEqual(db.get_offset(self.conn), 15)
        self.go(tg)
        self.assertEqual(tg.offsets[-1], 15)
        self.assertEqual(len(tg.sent), 4)  # updates not replayed

    def test_new_route_checked_same_run(self):
        tg = FakeTelegram([upd(1, "/add lille nantes 15/11")])
        self.go(tg, ("p", FakeSource([train()])))
        self.assertEqual(len(db.list_routes(self.conn)), 2)
        self.assertEqual(len(tg.sent), 2)  # confirmation + alert

    def test_now_sends_everything(self):
        s = ("p", FakeSource([train()]))
        tg = FakeTelegram()
        self.go(tg, s)
        tg.updates = [upd(1, "/now")]
        self.go(tg, s)
        self.assertEqual(len(tg.sent), 3)  # alert, ack, full availability
        self.assertIn("TGV 6603", tg.sent[2][1])

    def test_now_with_nothing(self):
        tg = FakeTelegram([upd(1, "/now")])
        self.go(tg, ("p", FakeSource([])))
        self.assertIn("Aucune place", tg.sent[-1][1])

    def test_expired_routes_purged(self):
        old = db.add_route(self.conn, "lille", "nantes", "2026-10-01", "2026-11-01")
        src = FakeSource([])
        self.go(FakeTelegram(), ("p", src))
        self.assertEqual([r.id for r in db.list_routes(self.conn)], [self.route.id])
        self.assertEqual(src.calls, 1)
        self.assertNotIn(old.id, [r.id for r in db.list_routes(self.conn)])

    def test_route_list_groups_round_trips(self):
        conn = db.connect(":memory:")
        db.add_route(conn, "montpellier", "lyon", "2026-11-05", "2026-11-05", "04:00", "08:00")
        db.add_route(conn, "montpellier", "lyon", "2026-11-04", "2026-11-04", "17:00", "20:00")
        db.add_route(conn, "lyon", "montpellier", "2026-11-05", "2026-11-05", "17:00", "22:00")
        db.add_route(conn, "montpellier", "lyon", "2026-11-12", "2026-11-12")
        self.assertEqual(run.format_route_list(db.list_routes(conn)), "\n".join([
            "🔁 Montpellier Saint-Roch ↔ Lyon",
            "",
            "Semaine du 02/11",
            "Aller :",
            "  #2 · mercredi 04/11 · 17:00-20:00",
            "  #1 · jeudi 05/11 · 04:00-08:00",
            "Retour :",
            "  #3 · jeudi 05/11 · 17:00-22:00",
            "",
            "Semaine du 09/11",
            "Aller :",
            "  #4 · jeudi 12/11",
        ]))
        conn.close()

    def test_weekly_route_from_command_filters_and_lists(self):
        tg = FakeTelegram([upd(1, "/add montpellier lyon jeudi 04:00-08:00"), upd(2, "/list")])
        s = FakeSource([train("1", day="2026-11-12", dep="06:29"), train("2", day="2026-11-11", dep="06:29")])
        db.delete_route(self.conn, self.route.id)
        self.go(tg, ("p", s))
        self.assertIn("chaque jeudi", tg.sent[0][1])
        self.assertIn("Chaque semaine", tg.sent[1][1])
        self.assertIn("TGV 1", tg.sent[2][1])
        self.assertNotIn("TGV 2", tg.sent[2][1])

    def test_dry_run_mode_does_not_persist(self):
        tg = run.PrintClient()
        self.go(tg, ("p", FakeSource([train()])), persist=False, process_commands=False)
        self.assertEqual(db.get_notified(self.conn, self.route.id), set())


if __name__ == "__main__":
    unittest.main()
