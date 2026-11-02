import json
import os
import unittest
from unittest import mock

import httpx

from models import Route, SourceError
from sources import opendata, planner

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


ROUTE = Route(1, "paris", "lyon", "2026-11-15", "2026-11-16")


class OpenDataTests(unittest.TestCase):
    def test_parse_records(self):
        trains = opendata.parse_records(load("opendata_sample.json"))
        # the NON row and the row without train_no are dropped
        self.assertEqual([(t.date, t.train_no, t.dep, t.arr) for t in trains],
                         [("2026-11-15", "6603", "07:56", "09:52"),
                          ("2026-11-15", "06609", "12:56", "14:52")])

    def test_parse_bad_shape(self):
        with self.assertRaises(SourceError):
            opendata.parse_records({"error": "nope"})

    def test_where_clause(self):
        w = opendata.build_where("PARIS (intramuros)", "LYON (intramuros)", "2026-11-15", "2026-11-16")
        self.assertIn('origine="PARIS (intramuros)"', w)
        self.assertIn('od_happy_card="OUI"', w)
        self.assertIn("date>=date'2026-11-15'", w)

    def test_fetch_paginates(self):
        calls = []

        def handler(req):
            off = int(req.url.params["offset"])
            calls.append(off)
            n = 100 if off == 0 else 5
            rows = [{"date": "2026-11-15", "train_no": str(off + i), "origine": "A", "destination": "B",
                     "heure_depart": f"{(off + i) // 60 % 24:02d}:{(off + i) % 60:02d}",
                     "heure_arrivee": "23:59", "od_happy_card": "OUI"} for i in range(n)]
            return httpx.Response(200, json={"total_count": 105, "results": rows})

        trains = opendata.fetch_available(ROUTE, client=client_for(handler))
        self.assertEqual(calls, [0, 100])
        self.assertEqual(len(trains), 105)

    def test_fetch_errors(self):
        for handler in (lambda r: httpx.Response(403, text="forbidden"),
                        lambda r: httpx.Response(200, text="<html>not json</html>"),
                        mock.Mock(side_effect=httpx.ConnectError("boom"))):
            with self.assertRaises(SourceError):
                opendata.fetch_available(ROUTE, client=client_for(handler))

    def test_raw_code_has_no_label(self):
        with self.assertRaises(SourceError):
            opendata.fetch_available(Route(1, "FRPLY", "FRLYS", "2026-11-15", "2026-11-15"),
                                     client=client_for(lambda r: httpx.Response(200, json={})))


class PlannerTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(planner, "_sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def test_parse_sample(self):
        trains = planner.parse_response(load("planner_sample.json"), "paris", "lyon")
        self.assertEqual([(t.date, t.dep, t.arr, t.train_no, t.seats) for t in trains],
                         [("2026-11-15", "07:56", "09:52", "6603", 3),
                          ("2026-11-15", "12:56", "14:52", "6609", None)])  # 6607 has 0 seats

    def test_parse_empty(self):
        self.assertEqual(planner.parse_response([]), [])
        self.assertEqual(planner.parse_response({"proposals": []}), [])

    def test_fetch_one_call_per_day(self):
        bodies = []

        def handler(req):
            bodies.append(json.loads(req.content))
            return httpx.Response(200, json=load("planner_sample.json"))

        trains = planner.fetch_available(ROUTE, client=client_for(handler))
        self.assertEqual([b["departureDateTime"][:10] for b in bodies], ["2026-11-15", "2026-11-16"])
        self.assertEqual(bodies[0]["origin"], "FRPAR")
        self.assertEqual(self.sleep.call_count, 1)
        self.assertEqual(len(trains), 2)  # same fixture twice -> deduplicated

    def test_blocked_or_captcha(self):
        for handler in (lambda r: httpx.Response(403, text="blocked"),
                        lambda r: httpx.Response(200, text="<html>captcha</html>",
                                                 headers={"content-type": "text/html"}),
                        lambda r: httpx.Response(200, text="{oops", headers={"content-type": "application/json"}),
                        mock.Mock(side_effect=httpx.ReadTimeout("slow"))):
            with self.assertRaises(SourceError):
                planner.fetch_available(Route(1, "paris", "lyon", "2026-11-15", "2026-11-15"),
                                        client=client_for(handler))

    def test_range_too_long(self):
        with self.assertRaises(SourceError):
            planner.fetch_available(Route(1, "paris", "lyon", "2026-11-15", "2027-03-01"),
                                    client=client_for(lambda r: httpx.Response(200, json={})))


if __name__ == "__main__":
    unittest.main()
