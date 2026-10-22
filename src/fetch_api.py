"""Optional: fetch employee skill profiles straight from the Cinode REST API.

UNTESTED. The cloud environment cannot reach api.cinode.com, so this is meant to
run on a machine that can (e.g. Robert's Mac). It needs a company integration
token (Administration > Integrations > Tokens); personal API accounts are disabled.

    export CINODE_ACCESS_ID=...  CINODE_ACCESS_SECRET=...  CINODE_COMPANY_ID=4041
    python src/fetch_api.py --max-calls 300

Behaviour:
  * Every response is cached under data/raw/ and never fetched again.
  * Stops at --max-calls, or when Cinode reports fewer than --reserve daily
    requests left (x-daily-requests-left), or on HTTP 429. Rerun the next day
    to resume where it stopped.
  * Read-only: only GET requests (plus the token call).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
API = os.environ.get("CINODE_API", "https://api.cinode.com")
API_VERSION = os.environ.get("CINODE_API_VERSION", "v0.1")


class QuotaStop(Exception):
    pass


class Client:
    def __init__(self, max_calls: int, reserve: int):
        self.company = os.environ["CINODE_COMPANY_ID"]
        self.max_calls, self.reserve, self.calls = max_calls, reserve, 0
        self.token = self._token()

    def _token(self) -> str:
        creds = f"{os.environ['CINODE_ACCESS_ID']}:{os.environ['CINODE_ACCESS_SECRET']}"
        req = urllib.request.Request(f"{API}/token", headers={
            "Authorization": "Basic " + base64.b64encode(creds.encode()).decode()})
        with urllib.request.urlopen(req) as r:
            return json.load(r)["access_token"]

    def get(self, path: str):
        if self.calls >= self.max_calls:
            raise QuotaStop(f"reached --max-calls={self.max_calls}")
        req = urllib.request.Request(f"{API}/{API_VERSION}/companies/{self.company}/{path}",
                                     headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req) as r:
                self.calls += 1
                left = r.headers.get("x-daily-requests-left")
                body = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise QuotaStop("HTTP 429 from Cinode (quota or rate limit)") from e
            raise
        if left is not None and int(left) < self.reserve:
            raise QuotaStop(f"only {left} daily requests left (reserve {self.reserve})")
        time.sleep(0.1)  # stay well under ~40 requests / 2 s
        return body


def cached(path: Path, fetch):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=300)
    ap.add_argument("--reserve", type=int, default=2000,
                    help="stop when fewer daily requests than this remain")
    args = ap.parse_args()
    client = Client(args.max_calls, args.reserve)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        users = cached(RAW / "catalog" / "users.json", lambda: client.get("users"))
        for u in users:
            uid = u["companyUserId"]
            out = RAW / "profile_skills" / f"{uid}.json"
            cached(out, lambda: {"_fetched": now, "company_user_id": uid,
                                 "skills": client.get(f"users/{uid}/skills")})
    except QuotaStop as e:
        print(f"Stopped after {client.calls} calls: {e}. Rerun later to resume.")
        return 0
    print(f"Done, {client.calls} calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
