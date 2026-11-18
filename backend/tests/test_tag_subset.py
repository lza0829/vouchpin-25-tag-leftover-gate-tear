"""Tag-subset sealing under the single global policy (subset_closes_day).

Run from ``backend``:  python -m unittest discover -s tests -v
Only the stdlib + runtime deps are required (no pytest / httpx).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from fastapi import HTTPException  # noqa: E402


class TagSubsetPolicyTest(unittest.TestCase):
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
        conn.commit()
        conn.close()

        from app.services import event_service, redeem_service, catalog_service, tag_service

        self.events = event_service
        self.redeem = redeem_service
        self.catalog = catalog_service
        self.tag_service = tag_service

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("DATA_DIR", None)

    def redeem_fact(self, code: str, tag: str, day: str | None = None) -> dict:
        return self.events.add_event(
            {
                "event_type": "redeem",
                "coupon_code": code,
                "campaign_code": "NEW10",
                "business_day": day or self.day,
                "tag": tag,
            }
        )

    # --- the requested check: nail one tag, then try another tag -----------
    def test_seal_one_tag_then_other_tag_is_blocked_everywhere(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("S-2", "store")
        self.redeem_fact("A-1", "app")

        # nail a single tag; the app leftover must be closed out in the same step
        res = self.redeem.seal(
            {"business_day": self.day, "tag": "store", "leftover_action": "void"}
        )
        self.assertEqual(res["tag"], "store")
        self.assertEqual(res["fact_count"], 2)
        self.assertEqual(res["leftover_tags"], ["app"])

        # now try to redeem another tag -> blocked by the day gate
        with self.assertRaises(HTTPException) as ctx:
            self.redeem_fact("A-2", "app")
        self.assertEqual(ctx.exception.status_code, 409)

        # try to nail another tag -> also blocked
        with self.assertRaises(HTTPException) as ctx2:
            self.redeem.seal({"business_day": self.day, "tag": "app"})
        self.assertEqual(ctx2.exception.status_code, 409)

    def test_store_seal_with_leftover_requires_explicit_action(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        with self.assertRaises(HTTPException) as ctx:
            self.redeem.seal({"business_day": self.day, "tag": "store"})
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["leftover_tags"], ["app"])

        res = self.redeem.seal(
            {"business_day": self.day, "tag": "store", "leftover_action": "carry"}
        )
        self.assertEqual(res["tag"], "store")
        self.assertEqual(res["fact_count"], 1)
        self.assertEqual(res["leftover_tags"], ["app"])
        self.assertTrue(res["day_closed"])
        self.assertEqual(res["leftover_closure"]["action"], "carry")

    def test_after_subset_seal_day_gate_blocks_other_tag_and_same_tag(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem.seal({"business_day": self.day, "tag": "store", "leftover_action": "void"})

        # other tag cannot be redeemed into the day
        with self.assertRaises(HTTPException) as ctx:
            self.redeem_fact("A-1", "app")
        self.assertEqual(ctx.exception.status_code, 409)

        # other tag cannot be sealed
        with self.assertRaises(HTTPException) as ctx2:
            self.redeem.seal({"business_day": self.day, "tag": "app"})
        self.assertEqual(ctx2.exception.status_code, 409)

        # same tag cannot be sealed twice
        with self.assertRaises(HTTPException) as ctx3:
            self.redeem.seal({"business_day": self.day, "tag": "store"})
        self.assertEqual(ctx3.exception.status_code, 409)

        # whole-day seal is also blocked
        with self.assertRaises(HTTPException):
            self.redeem.seal({"business_day": self.day})

    def test_leftover_can_be_sealed_on_target_day(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        res = self.redeem.seal(
            {"business_day": self.day, "tag": "store", "leftover_action": "carry"}
        )
        target = res["leftover_closure"]["target_day"]
        self.assertNotEqual(target, self.day)

        carried = self.redeem.preview(target, "app")
        self.assertEqual(carried["open_count"], 1)
        self.assertFalse(carried["gate"]["sealed"])
        sealed = self.redeem.seal({"business_day": target, "tag": "app"})
        self.assertEqual(sealed["business_day"], target)
        self.assertEqual(sealed["fact_count"], 1)

    def test_void_closure_leaves_nothing_open(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        self.redeem.seal(
            {"business_day": self.day, "tag": "store", "leftover_action": "void"}
        )
        d = self.catalog.dashboard()
        self.assertEqual(d["open_fact_count"], 0)
        by_tag = {r["tag"]: r for r in d["tag_overview"]}
        self.assertEqual(by_tag["app"]["voided_count"], 1)
        self.assertEqual(by_tag["store"]["sealed_count"], 1)

    def test_no_tag_seal_takes_all_open_and_records_no_leftovers(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        p = self.redeem.preview(self.day, None)
        self.assertEqual(p["open_count"], 2)
        self.assertFalse(p["requires_leftover_action"])
        res = self.redeem.seal({"business_day": self.day})
        self.assertIsNone(res["tag"])
        self.assertEqual(res["fact_count"], 2)
        self.assertEqual(res["leftover_tags"], [])

    def test_batch_snapshot_and_tag_overview_agree(self) -> None:
        self.redeem_fact("S-1", "store")
        self.redeem_fact("A-1", "app")
        res = self.redeem.seal(
            {"business_day": self.day, "tag": "store", "leftover_action": "carry"}
        )
        batches = self.redeem.list_batches(self.day)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["id"], res["batch_id"])
        self.assertEqual(batches[0]["tag"], "store")
        self.assertEqual(batches[0]["leftover_tags"], ["app"])
        self.assertEqual(batches[0]["fact_count"], 1)

        overview = {r["tag"]: r for r in self.catalog.dashboard()["tag_overview"]}
        self.assertTrue(overview["store"]["sealed_today"])
        self.assertEqual(overview["store"]["sealed_count"], 1)

    def test_empty_tag_is_a_real_bucket_not_a_wildcard(self) -> None:
        self.redeem_fact("E-1", "")
        self.redeem_fact("S-1", "store")
        p = self.redeem.preview(self.day, "")
        self.assertEqual(p["open_count"], 1)
        self.assertEqual(p["facts"][0]["coupon_code"], "E-1")
        self.assertEqual(p["leftover_tags"], ["store"])
        res = self.redeem.seal(
            {"business_day": self.day, "tag": "", "leftover_action": "void"}
        )
        self.assertEqual(res["tag"], "")
        self.assertEqual(res["fact_count"], 1)

    def test_policy_is_the_declared_one(self) -> None:
        from app import config

        self.assertEqual(config.TAG_SEAL_POLICY, "subset_closes_day")


if __name__ == "__main__":
    unittest.main()
