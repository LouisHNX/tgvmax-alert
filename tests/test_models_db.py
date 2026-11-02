import unittest

import db
from models import Route, Train


def train(date="2026-11-15", dep="08:00", no="6603"):
    return Train(date=date, dep=dep, arr="10:00", train_no=no, origin="A", dest="B")


class RouteAcceptsTests(unittest.TestCase):
    def test_dates(self):
        r = Route(1, "paris", "lyon", "2026-11-15", "2026-11-17")
        self.assertTrue(r.accepts(train("2026-11-15")))
        self.assertTrue(r.accepts(train("2026-11-17")))
        self.assertFalse(r.accepts(train("2026-11-14")))
        self.assertFalse(r.accepts(train("2026-11-18")))

    def test_time_window_inclusive(self):
        r = Route(1, "paris", "lyon", "2026-11-15", "2026-11-15", "08:00", "12:00")
        self.assertTrue(r.accepts(train(dep="08:00")))
        self.assertTrue(r.accepts(train(dep="12:00")))
        self.assertFalse(r.accepts(train(dep="07:59")))
        self.assertFalse(r.accepts(train(dep="12:01")))

    def test_no_window(self):
        r = Route(1, "paris", "lyon", "2026-11-15", "2026-11-15")
        self.assertTrue(r.accepts(train(dep="23:59")))


class DbTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_offset(self):
        self.assertEqual(db.get_offset(self.conn), 0)
        db.set_offset(self.conn, 42)
        db.set_offset(self.conn, 43)
        self.assertEqual(db.get_offset(self.conn), 43)

    def test_routes_crud(self):
        r = db.add_route(self.conn, "paris", "lyon", "2026-11-15", "2026-11-16", "08:00", "12:00")
        self.assertEqual(db.list_routes(self.conn), [r])
        self.assertTrue(db.delete_route(self.conn, r.id))
        self.assertFalse(db.delete_route(self.conn, r.id))
        self.assertEqual(db.list_routes(self.conn), [])

    def test_notified_replace_and_cascade(self):
        r = db.add_route(self.conn, "paris", "lyon", "2026-11-15", "2026-11-16")
        db.replace_notified(self.conn, r.id, {("2026-11-15", "1"), ("2026-11-15", "2")})
        db.replace_notified(self.conn, r.id, {("2026-11-15", "2"), ("2026-11-16", "3")})
        self.assertEqual(db.get_notified(self.conn, r.id), {("2026-11-15", "2"), ("2026-11-16", "3")})
        db.delete_route(self.conn, r.id)
        self.assertEqual(db.get_notified(self.conn, r.id), set())

    def test_purge_expired(self):
        old = db.add_route(self.conn, "paris", "lyon", "2026-10-01", "2026-10-31")
        edge = db.add_route(self.conn, "paris", "lyon", "2026-11-01", "2026-11-02")
        db.replace_notified(self.conn, old.id, {("2026-10-01", "1")})
        self.assertEqual(db.purge_expired(self.conn, "2026-11-02"), [old.id])
        self.assertEqual([r.id for r in db.list_routes(self.conn)], [edge.id])
        self.assertEqual(db.get_notified(self.conn, old.id), set())


if __name__ == "__main__":
    unittest.main()
