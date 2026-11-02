import unittest
from datetime import date

from telegram import CommandError, parse_command, split_text

TODAY = date(2026, 11, 2)


class ParseCommandTests(unittest.TestCase):
    def test_not_a_command(self):
        self.assertIsNone(parse_command("bonjour", TODAY))
        self.assertIsNone(parse_command("", TODAY))

    def test_simple_commands(self):
        for name in ("list", "now", "help"):
            self.assertEqual(parse_command(f"/{name}", TODAY).name, name)
        self.assertEqual(parse_command("/start", TODAY).name, "help")
        self.assertEqual(parse_command("/list@my_bot", TODAY).name, "list")

    def test_add_single_day(self):
        c = parse_command("/add paris lyon 15/11", TODAY)
        self.assertEqual((c.name, c.origin, c.dest), ("add", "paris", "lyon"))
        self.assertEqual((c.date_from, c.date_to), ("2026-11-15", "2026-11-15"))
        self.assertIsNone(c.time_from)

    def test_add_range_and_window(self):
        c = parse_command("/add Paris LYON 15/11-20/11 08:00-12:00", TODAY)
        self.assertEqual((c.origin, c.dest), ("paris", "lyon"))
        self.assertEqual((c.date_from, c.date_to), ("2026-11-15", "2026-11-20"))
        self.assertEqual((c.time_from, c.time_to), ("08:00", "12:00"))

    def test_add_iso_and_range_iso(self):
        c = parse_command("/add paris lyon 2026-12-24", TODAY)
        self.assertEqual((c.date_from, c.date_to), ("2026-12-24", "2026-12-24"))
        c = parse_command("/add paris lyon 2026-12-24-2026-12-26", TODAY)
        self.assertEqual((c.date_from, c.date_to), ("2026-12-24", "2026-12-26"))

    def test_next_occurrence(self):
        c = parse_command("/add paris lyon 15/01", TODAY)
        self.assertEqual(c.date_from, "2027-01-15")
        c = parse_command("/add paris lyon 02/11", TODAY)  # today counts
        self.assertEqual(c.date_from, "2026-11-02")
        c = parse_command("/add paris lyon 28/12-03/01", TODAY)  # range across new year
        self.assertEqual((c.date_from, c.date_to), ("2026-12-28", "2027-01-03"))

    def test_add_weekly(self):
        c = parse_command("/add montpellier lyon Jeudi 04:00-08:00", TODAY)
        self.assertEqual((c.weekday, c.date_from, c.date_to), (3, "2026-11-02", "2099-12-31"))
        self.assertEqual((c.time_from, c.time_to), ("04:00", "08:00"))
        self.assertIsNone(parse_command("/add paris lyon 15/11", TODAY).weekday)

    def test_raw_planner_code(self):
        c = parse_command("/add frply frlys 15/11", TODAY)
        self.assertEqual((c.origin, c.dest), ("FRPLY", "FRLYS"))

    def test_unknown_station_gives_hint(self):
        with self.assertRaises(CommandError) as cm:
            parse_command("/add atlantis lyon 15/11", TODAY)
        self.assertIn("Gare inconnue", str(cm.exception))
        self.assertIn("paris", str(cm.exception))

    def test_bad_inputs(self):
        for text in ("/add paris lyon", "/add paris lyon 31/02", "/add paris lyon demain",
                     "/add paris lyon 2026-10-01", "/add paris lyon 20/11-15/11x",
                     "/add paris lyon 15/11 12:00-08:00", "/add paris lyon 15/11 8h-12h",
                     "/del", "/del abc", "/nope"):
            with self.assertRaises(CommandError, msg=text):
                parse_command(text, TODAY)

    def test_del(self):
        c = parse_command("/del 3", TODAY)
        self.assertEqual((c.name, c.route_id), ("del", 3))

    def test_split_text(self):
        text = "\n".join(["x" * 50] * 100)
        chunks = split_text(text, limit=500)
        self.assertTrue(all(len(c) <= 500 for c in chunks))
        self.assertEqual("\n".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
