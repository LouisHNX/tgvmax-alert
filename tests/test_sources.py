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
        self.assertEqual((trains[0].origin, trains[0].dest), ("Paris Gare de Lyon", "Lyon Part-Dieu"))
        # the NON rows are dropped; 6641 has one row per Lyon station
        self.assertEqual([(t.date, t.train_no, t.dep, t.arr) for t in trains],
                         [("2026-11-05", "6641", "06:20", "08:22"),
                          ("2026-11-05", "6641", "06:20", "08:34")])

    def test_parse_drops_row_without_train_no(self):
        row = {"date": "2026-11-05", "train_no": None, "heure_depart": "06:20", "od_happy_card": "OUI"}
        self.assertEqual(opendata.parse_records({"results": [row]}), [])

    def test_fetch_keeps_first_arrival_per_train(self):
        rows = [r for r in load("opendata_sample.json")["results"] if r["od_happy_card"] == "OUI"]
        trains = opendata.fetch_available(
            ROUTE, client=client_for(lambda r: httpx.Response(200, json={"results": rows})))
        self.assertEqual([t.arr for t in trains], ["08:22"])

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
        self.assertEqual(len(trains), 6)
        t = trains[0]
        self.assertEqual((t.date, t.dep, t.arr, t.train_no, t.seats),
                         ("2026-11-07", "17:00", "18:56", "6687", 36))

    def test_parse_skips_zero_seats(self):
        p = {"dep": "2026-11-07T17:00", "arr": "2026-11-07T18:56", "num": "6687", "count": 0}
        self.assertEqual(planner.parse_response({"proposals": [p]}), [])

    def test_parse_empty(self):
        self.assertEqual(planner.parse_response({"proposals": []}), [])

    def test_parse_unknown_shape_fails(self):
        # must reach the fallback, not look like "no seats"
        for bad in ([], {"error": "nope"}):
            with self.assertRaises(SourceError):
                planner.parse_response(bad)

    def test_fetch_one_call_per_day(self):
        params = []

        def handler(req):
            self.assertEqual(req.method, "GET")
            params.append(req.url.params)
            return httpx.Response(200, json=load("planner_sample.json"))

        route = Route(1, "paris-lyon", "marseille", "2026-11-15", "2026-11-16")
        trains = planner.fetch_available(route, client=client_for(handler))
        self.assertEqual([p["departureDateTime"] for p in params],
                         ["2026-11-15T01:00:00.000Z", "2026-11-16T01:00:00.000Z"])
        self.assertEqual((params[0]["origin"], params[0]["destination"]), ("FRPLY", "FRMSC"))
        self.assertEqual(self.sleep.call_count, 1)
        self.assertEqual(len(trains), 6)  # same fixture twice -> deduplicated

    def test_fetch_city_uses_open_data_pairs(self):
        asked = []

        def handler(req):
            if req.url.host == "data.sncf.com":
                self.assertIn('origine="PARIS (intramuros)"', req.url.params["where"])
                return httpx.Response(200, json={"results": [
                    {"origine_iata": "FRPLY", "destination_iata": "FRLPE"},
                    {"origine_iata": "FRPLY", "destination_iata": "FRLPD"}]})
            asked.append(req.url.params["destination"])
            return httpx.Response(200, json={"proposals": []})

        planner.fetch_available(Route(1, "paris", "lyon", "2026-11-15", "2026-11-15"),
                                client=client_for(handler))
        self.assertEqual(asked, ["FRLPD", "FRLPE"])

    def test_blocked_or_captcha(self):
        for handler in (lambda r: httpx.Response(403, text="blocked"),
                        lambda r: httpx.Response(200, text="<html>captcha</html>",
                                                 headers={"content-type": "text/html"}),
                        lambda r: httpx.Response(200, text="{oops", headers={"content-type": "application/json"}),
                        mock.Mock(side_effect=httpx.ReadTimeout("slow"))):
            with self.assertRaises(SourceError):
                planner.fetch_available(Route(1, "paris-lyon", "marseille", "2026-11-15", "2026-11-15"),
                                        client=client_for(handler))

    def test_range_too_long(self):
        with self.assertRaises(SourceError):
            planner.fetch_available(Route(1, "paris", "lyon", "2026-11-15", "2027-03-01"),
                                    client=client_for(lambda r: httpx.Response(200, json={})))


if __name__ == "__main__":
    unittest.main()
