"""Optional: fetch employee skill profiles straight from the Cinode REST API.

The cloud environment cannot reach api.cinode.com, so this is meant to run on a
machine that can (e.g. Robert's Mac). Credentials come from the environment or a
local, gitignored .env file in the repo root:

    CINODE_API_TOKEN=...      # company integration access token (JWT), used as Bearer
    # or CINODE_ACCESS_ID / CINODE_ACCESS_SECRET, exchanged at /token
    CINODE_COMPANY_ID=4041    # optional, defaults to 4041

    python src/fetch_api.py --max-calls 300

Only employees (companyUserType 0) get a profile file.

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
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import http.client
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
API = os.environ.get("CINODE_API", "https://api.cinode.com")
API_VERSION = os.environ.get("CINODE_API_VERSION", "v0.1")


EMPLOYEE = 0  # companyUserType


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal KEY=VALUE parser; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class QuotaStop(Exception):
    pass


class Client:
    def __init__(self, max_calls: int, reserve: int):
        self.company = os.environ.get("CINODE_COMPANY_ID", "4041")
        self.max_calls, self.reserve, self.calls, self.left = max_calls, reserve, 0, None
        self.token = self._token()

    def _token(self) -> str:
        if os.environ.get("CINODE_API_TOKEN"):
            return os.environ["CINODE_API_TOKEN"]
        creds = f"{os.environ['CINODE_ACCESS_ID']}:{os.environ['CINODE_ACCESS_SECRET']}"
        req = urllib.request.Request(f"{API}/token", headers={
            "Authorization": "Basic " + base64.b64encode(creds.encode()).decode()})
        with urllib.request.urlopen(req) as r:
            return json.load(r)["access_token"]

    def get(self, path: str):
        req = urllib.request.Request(f"{API}/{API_VERSION}/companies/{self.company}/{path}",
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Accept-Encoding": "gzip"})
        for attempt in range(3):
            if self.calls >= self.max_calls:
                raise QuotaStop(f"reached --max-calls={self.max_calls}")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    self.calls += 1
                    left = r.headers.get("x-daily-requests-left")
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    body = json.loads(raw)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise QuotaStop("HTTP 429 from Cinode (quota or rate limit)") from e
                raise
            except http.client.IncompleteRead:  # large bodies are sometimes cut short
                if attempt == 2:
                    raise
                time.sleep(2)
        self.left = left
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


def skill_row(s: dict) -> dict:
    """Keep the raw API fields and add the flat ones load_duckdb.py expects."""
    kw = s.get("keyword") or {}
    return {**s,
            "keywordSynonymId": s.get("keywordSynonymId", kw.get("synonymId")),
            "keywordId": s.get("keywordId", kw.get("id")),
            "name": s.get("name") or kw.get("masterSynonym"),
            "level": s.get("level")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=300)
    ap.add_argument("--reserve", type=int, default=2000,
                    help="stop when fewer daily requests than this remain")
    args = ap.parse_args()
    load_env()
    client = Client(args.max_calls, args.reserve)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        users = cached(RAW / "catalog" / "users.json", lambda: client.get("users"))
        employees = [u for u in users if u.get("companyUserType") == EMPLOYEE]
        print(f"{len(employees)} employees of {len(users)} users")
        for u in employees:
            uid = u["companyUserId"]
            out = RAW / "profile_skills" / f"{uid}.json"
            cached(out, lambda: {"_fetched": now, "company_user_id": uid,
                                 "skills": [skill_row(s) for s in client.get(f"users/{uid}/skills")]})
    except QuotaStop as e:
        print(f"Stopped after {client.calls} calls: {e}. Rerun later to resume.")
        return 0
    print(f"Done, {client.calls} calls, {client.left} daily requests left.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
