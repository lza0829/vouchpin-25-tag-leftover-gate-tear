from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "Vouchpin"
API_PORT = 10800
DATA_DIR = Path(os.environ.get("DATA_DIR") or (Path(__file__).resolve().parents[2] / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "clipbond.db"

# Tag-subset sealing is a single, global policy.  The two readings are
# mutually exclusive and only one is carried out:
#
#   "subset_closes_day" (implemented)
#       Any sealed tag-subset batch closes the whole business day.  No more
#       redeem facts may be written and no other tag may be sealed.  Open
#       facts of the other tags are snapshotted on the batch as
#       leftover_tags and must be carried to an unsealed day or voided.
#
#   "subset_keeps_day_open" (deliberately NOT implemented)
#       A subset seal leaves the day gate open; other tags may keep being
#       redeemed and later sealed, and only the same tag may not be pinned
#       twice.
#
# Everything (tag filters, batch snapshot, day gate, dashboard roll-up and
# leftover closure) reads TAG_SEAL_POLICY through app/services/tag_service.py.
TAG_SEAL_POLICY = "subset_closes_day"
