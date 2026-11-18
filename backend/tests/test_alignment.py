"""Cross-pipeline alignment tests for the tag-subset incident.

The incident was a fork between the event tag index, the leftover
materialization table, the open-fact scan and the day write gate.  The fix
declares the **current event tag + open-fact scan** the single source of
truth; these tests prove that the leftover list, the gate and the dashboard
can never diverge from that scan, including tag changes and interleaved
nail/refresh traffic.

Run from ``backend``:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from fastapi import HTTPException  # noqa: E402


class AlignmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
            del sys.modules[mod]
        from app.seed import seed_if_empty
        from app.db import connect
        from app.repositories import settings as settings_repo

        seed_if_empty()
        conn = connect()
        self.day = settings_repo.get(conn, "business_day")
        for table in ("redeem_facts", "redeem_batches", "coupon_events"):
            conn.execute(f"DELETE FROM {table}")
        # Any stale gate flag from the old force-close pipeline must be gone.
        conn.execute("DELETE FROM settings WHERE key LIKE 'gate_force_closed:%'")
        conn.commit()
        conn.close()

        from app.services import event_service, redeem_service, catalog_service
        from app.db import connect as _connect

        self.events = event_service
        self.redeem = redeem_service
        self.catalog = catalog_service
        self.connect = _connect

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("DATA_DIR", None)

    # ------------------------------------------------------------------ utils
    def redeem_fact(self, code: str, tag: str, day: str | None = None) -> int:
        r = self.events.add_event(
            {
                "event_type": "redeem",
                "coupon_code": code,
                "campaign_code": "NEW10",
                "business_day": day or self.day,
                "tag": tag,
            }
        )
        return int(r["fact_id"])

    def event_id_of(self, code: str) -> int:
        rows = self.events.list_events(self.day)
        return next(int(r["id"]) for r in rows if r["coupon_code"] == code)

    def assert_list_equals_remaining_open(self, day: str, tag: str | None) -> None:
        """Core invariant: leftover list == open facts whose tag != batch tag."""
        preview = self.redeem.preview(day, tag)
        conn = self.connect()
        try:
            from app.repositories import facts as fact_repo

            scan = fact_repo.leftover_facts(conn, day, tag) if tag is not None else []
        finally:
            conn.close()
        self.assertEqual(
            sorted(r["coupon_code"] for r in preview["leftover_rows"]),
            sorted(r["coupon_code"] for r in scan),
        )
        self.assertEqual(preview["leftover_count"], len(scan))
        self.assertEqual(
            preview["leftover_total"],
            round(sum(float(r["amount"]) for r in scan), 2),
        )
        # And every row's money/coupon matches the scan row for row.
        scan_by_code = {r["coupon_code"]: r for r in scan}
        for row in preview["leftover_rows"]:
            self.assertEqual(float(row["amount"]), float(scan_by_code[row["coupon_code"]]["amount"]))
            self.assertEqual(row["tag"], scan_by_code[row["coupon_code"]]["tag"])

    def assert_no_torn_state(self, day: str) -> None:
        """The two forbidden end-states must never be observable."""
        conn = self.connect()
        try:
            from app.repositories import facts as fact_repo
            from app.services import tag_service

            opens = fact_repo.open_facts_for_day(conn, day)
            gate = tag_service.day_gate(conn, day)
            nailable = tag_service.can_nail(conn, day)
        finally:
            conn.close()
        preview = self.redeem.preview(day)
        dash = self.catalog.dashboard()
        # 1) leftover != remaining open is forbidden.
        self.assertEqual(
            sorted(f["coupon_code"] for f in opens),
            sorted(f["coupon_code"] for f in preview["facts"]),
        )
        self.assertEqual(dash["open_fact_count"], len(opens))
        # 2) gate state vs nailability must not contradict each other.
        self.assertEqual(nailable, preview["can_nail"])
        self.assertEqual(nailable, dash["can_nail"])
        if gate["sealed"]:
            self.assertFalse(nailable, "关闸日不得显示可钉")
            self.assertEqual(preview["gate"]["sealed"], True)

    # ------------------------------------------------- 1. list extra/missing codes
    def test_leftover_list_has_no_extra_and_no_missing_codes(self) -> None:
        for i in range(4):
            self.redeem_fact(f"S-{i}", "store")
        for i in range(3):
            self.redeem_fact(f"A-{i}", "app")
        # The old materialization silently dropped the newest coupon code;
        # every one of the 3 app facts must show up, including the last.
        self.assert_list_equals_remaining_open(self.day, "store")
        preview = self.redeem.preview(self.day, "store")
        self.assertEqual(
            sorted(r["coupon_code"] for r in preview["leftover_rows"]),
            ["A-0", "A-1", "A-2"],
        )
        self.assertEqual(preview["leftover_count"], 3)
        self.assert_list_equals_remaining_open(self.day, "app")
        self.assert_no_torn_state(self.day)

    # ------------------------------------------------- 2. stale list after retag
    def test_tag_change_repartitions_fact_and_list_follows_immediately(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        eid = self.event_id_of("A-1")

        # Move the app fact into the store partition.
        res = self.events.retag(eid, {"tag": "store"})
        self.assertTrue(res["fact_retagged"])

        conn = self.connect()
        try:
            from app.repositories import facts as fact_repo, events as event_repo

            event = event_repo.list_events(conn, self.day, tag="store")
            fact = fact_repo.list_facts(conn, self.day, tag="store", status="open")
        finally:
            conn.close()
        # Event index and fact partition move together.
        self.assertEqual({r["coupon_code"] for r in event}, {"S-1", "A-1"})
        self.assertEqual({r["coupon_code"] for r in fact}, {"S-1", "A-1"})

        # A store-subset seal now has NO leftovers; an app-subset preview is empty.
        preview_store = self.redeem.preview(self.day, "store")
        self.assertEqual(preview_store["leftover_count"], 0)
        self.assertEqual(preview_store["open_count"], 2)
        preview_app = self.redeem.preview(self.day, "app")
        self.assertEqual(preview_app["open_count"], 0)
        self.assert_no_torn_state(self.day)

        # And the whole-day seal succeeds without any leftover action.
        sealed = self.redeem.seal({"business_day": self.day})
        self.assertEqual(sealed["fact_count"], 2)
        self.assertEqual(sealed["leftover_tags"], [])
        self.assert_no_torn_state(self.day)

    def test_retag_moves_money_into_the_new_partition(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        self.events.retag(self.event_id_of("A-1"), {"tag": "store"})
        self.assert_list_equals_remaining_open(self.day, "store")
        overview = {r["tag"]: r for r in self.catalog.dashboard()["tag_overview"]}
        self.assertEqual(overview["store"]["open_count"], 2)
        self.assertNotIn("app", overview)

    def test_retag_on_closed_day_is_refused(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        self.redeem.seal(
            {"business_day": self.day, "tag": "store", "leftover_action": "void"}
        )
        with self.assertRaises(HTTPException) as ctx:
            self.events.retag(self.event_id_of("S-1"), {"tag": "app"})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_retag_unknown_event_is_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.events.retag(99999, {"tag": "store"})
        self.assertEqual(ctx.exception.status_code, 404)

    # ------------------------------------------------- 3. closed gate still nail-able
    def test_closed_gate_never_offers_nailing_and_write_path_agrees(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        self.redeem.seal(
            {"business_day": self.day, "tag": "store", "leftover_action": "void"}
        )

        # Every read surface says: not nailable.
        self.assertFalse(self.redeem.preview(self.day)["can_nail"])
        self.assertFalse(self.catalog.dashboard()["can_nail"])

        # And every write path actually rejects with 409 -- no UI/backend drift.
        with self.assertRaises(HTTPException) as ctx:
            self.redeem_fact("A-9", "app")
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(HTTPException) as ctx2:
            self.redeem.seal({"business_day": self.day, "tag": "app"})
        self.assertEqual(ctx2.exception.status_code, 409)
        self.assert_no_torn_state(self.day)

    def test_legacy_force_close_flag_cannot_close_an_open_day(self) -> None:
        self.redeem_fact("S-1", "store")
        conn = self.connect()
        try:
            from app.repositories import settings as settings_repo
            settings_repo.set_(conn, f"gate_force_closed:{self.day}", "1")
            conn.commit()
        finally:
            conn.close()
        # The deprecated flag has no authority: gate stays open and nailable.
        self.assertTrue(self.redeem.preview(self.day)["can_nail"])
        sealed = self.redeem.seal({"business_day": self.day})
        self.assertEqual(sealed["fact_count"], 1)

    # ------------------------------------------------- 4. open day, empty list but open rows
    def test_open_gate_with_empty_filtered_list_still_shows_real_open(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("S-2", "store")
        # Filtering by a tag that has no open facts gives an empty page, but
        # the day is open and the unfiltered scan still shows the real opens.
        app_view = self.redeem.preview(self.day, "app")
        self.assertEqual(app_view["open_count"], 0)
        self.assertTrue(app_view["can_nail"])
        full = self.redeem.preview(self.day)
        self.assertEqual(full["open_count"], 2)
        self.assertTrue(full["can_nail"])
        self.assertEqual(self.catalog.dashboard()["open_fact_count"], 2)
        self.assert_no_torn_state(self.day)

    # ------------------------------------------------- 5. interleaved refresh / nail
    def test_interleaved_preview_and_seal_never_shows_empty_then_closes_gate(self) -> None:
        for i in range(6):
            self.redeem_fact(f"S-{i}", "store")
        for i in range(4):
            self.redeem_fact(f"A-{i}", "app")

        errors: list[Exception] = []
        stop = threading.Event()

        def hammer_previews() -> None:
            try:
                while not stop.is_set():
                    p = self.redeem.preview(self.day, "store")
                    # While the day is open, a store filter must always see the
                    # full set of store opens -- never a transient empty list.
                    if not p["gate"]["sealed"]:
                        if p["open_count"] != 6:
                            errors.append(
                                AssertionError(f"transient open_count={p['open_count']}")
                            )
                            return
                        if p["can_nail"] is not True:
                            errors.append(AssertionError("open gate reported not nailable"))
                            return
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)

        t = threading.Thread(target=hammer_previews)
        t.start()
        try:
            res = self.redeem.seal(
                {"business_day": self.day, "tag": "store", "leftover_action": "void"}
            )
        finally:
            stop.set()
            t.join()

        self.assertEqual(res["fact_count"], 6)
        self.assertEqual(errors, [])
        # After the seal: gate closed, nothing open, not nailable, everywhere.
        self.assert_no_torn_state(self.day)
        self.assertEqual(self.catalog.dashboard()["open_fact_count"], 0)
        self.assertFalse(self.redeem.preview(self.day)["can_nail"])

    def test_concurrent_seals_only_one_closes_the_day(self) -> None:
        for i in range(4):
            self.redeem_fact(f"S-{i}", "store")
        outcomes: list[str] = []

        import sqlite3

        def do_seal() -> None:
            try:
                self.redeem.seal({"business_day": self.day})
                outcomes.append("ok")
            except HTTPException as exc:
                outcomes.append(f"{exc.status_code}")
            except sqlite3.OperationalError as exc:
                # BEGIN IMMEDIATE serialization: a lost lock race is still a
                # refused write, never a second sealed batch.
                outcomes.append(f"locked:{exc}")

        threads = [threading.Thread(target=do_seal) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(outcomes).count("ok"), 1)
        self.assertTrue(all(o != "ok" for o in outcomes if o != "ok"))
        conn = self.connect()
        try:
            from app.repositories import batches as batch_repo, facts as fact_repo

            self.assertEqual(len(batch_repo.list_batches(conn, self.day)), 1)
            self.assertEqual(fact_repo.open_count_for_day(conn, self.day), 0)
        finally:
            conn.close()
        self.assert_no_torn_state(self.day)

    # ------------------------------------------------- 6. deprecated pipeline gone
    def test_materialized_tables_and_refresh_endpoint_are_gone(self) -> None:
        conn = self.connect()
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertNotIn("tag_index", tables)
            self.assertNotIn("leftover_material", tables)
            flags = conn.execute(
                "SELECT COUNT(*) c FROM settings WHERE key LIKE 'gate_force_closed:%'"
            ).fetchone()["c"]
            self.assertEqual(flags, 0)
        finally:
            conn.close()

        from app.main import app

        paths = {route.path for route in app.routes}
        self.assertNotIn("/api/tags/refresh", paths)
        self.assertIn("/api/events/{event_id}/tag", paths)

    # ------------------------------------------------- 7. carry keeps alignment
    def test_carry_then_target_day_lists_and_gate_stay_aligned(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        res = self.redeem.seal(
            {"business_day": self.day, "tag": "store", "leftover_action": "carry"}
        )
        target = res["leftover_closure"]["target_day"]
        self.assertEqual(res["leftover_closure"]["count"], 1)

        # Source day: closed, zero opens.
        self.assertEqual(self.catalog.dashboard()["open_fact_count"], 0)
        self.assertFalse(self.redeem.preview(self.day)["can_nail"])

        # Target day: the carried fact is the only open and is nailable.
        p = self.redeem.preview(target, "app")
        self.assertEqual(p["open_count"], 1)
        self.assertEqual(p["facts"][0]["coupon_code"], "A-1")
        self.assertTrue(p["can_nail"])
        self.assert_list_equals_remaining_open(target, "app")
        sealed = self.redeem.seal({"business_day": target, "tag": "app"})
        self.assertEqual(sealed["fact_count"], 1)
        self.assertFalse(self.redeem.preview(target)["can_nail"])


if __name__ == "__main__":
    unittest.main()
