"""
log_traffic.py
--------------
Pulls GitHub repository traffic (views, unique visitors, clones, unique cloners)
via the REST API and appends it to a CSV so the 14-day rolling window is preserved
as a permanent longitudinal record.

Runs inside GitHub Actions (see .github/workflows/traffic.yml).
Requires a fine-grained personal access token with "Administration: Read"
permission on the repository, stored as the repository secret TRAFFIC_TOKEN.

Output: traffic/traffic_log.csv with one row per calendar day.
Re-running is idempotent: existing dates are overwritten with the latest
values GitHub reports, new dates are appended.
"""

import csv
import json
import os
import sys
import urllib.request
from datetime import date
from pathlib import Path

API = "https://api.github.com"
OUT_PATH = Path("traffic/traffic_log.csv")
FIELDS = ["date", "views", "unique_visitors", "clones", "unique_cloners"]


def fetch(endpoint: str, repo: str, token: str) -> dict:
    """GET a traffic endpoint and return the parsed JSON."""
    req = urllib.request.Request(
        f"{API}/repos/{repo}/traffic/{endpoint}?per=day",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def load_existing() -> dict[str, dict]:
    """Read the existing CSV into a dict keyed by date (empty if absent)."""
    if not OUT_PATH.exists():
        return {}
    with OUT_PATH.open(newline="") as f:
        return {row["date"]: row for row in csv.DictReader(f)}


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("TRAFFIC_TOKEN")
    if not repo or not token:
        sys.exit("GITHUB_REPOSITORY and TRAFFIC_TOKEN must both be set.")

    views = fetch("views", repo, token)
    clones = fetch("clones", repo, token)

    rows = load_existing()

    # Merge views: timestamp looks like "2026-09-25T00:00:00Z"; keep the date part.
    for v in views.get("views", []):
        d = v["timestamp"][:10]
        rows.setdefault(d, {"date": d})
        rows[d]["views"] = v["count"]
        rows[d]["unique_visitors"] = v["uniques"]

    for c in clones.get("clones", []):
        d = c["timestamp"][:10]
        rows.setdefault(d, {"date": d})
        rows[d]["clones"] = c["count"]
        rows[d]["unique_cloners"] = c["uniques"]

    # Fill any gaps with 0 so every row has every column.
    for r in rows.values():
        for k in FIELDS[1:]:
            r.setdefault(k, 0)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for d in sorted(rows):
            w.writerow(rows[d])

    total_v = sum(int(r["views"]) for r in rows.values())
    total_c = sum(int(r["clones"]) for r in rows.values())
    print(f"{date.today()}: {len(rows)} days logged; "
          f"cumulative views={total_v}, cumulative clones={total_c}")


if __name__ == "__main__":
    main()
