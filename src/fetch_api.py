"""Optional: fetch employee skill profiles straight from the Cinode REST API.

The cloud environment cannot reach api.cinode.com, so this is meant to run on a
machine that can (e.g. Robert's Mac). Credentials come from the environment or a
local, gitignored .env file in the repo root:

    CINODE_API_TOKEN=...      # company integration access token (JWT), used as Bearer
    # or CINODE_ACCESS_ID / CINODE_ACCESS_SECRET, exchanged at /token
    CINODE_COMPANY_ID=4041    # optional, defaults to 4041

    uv run src/fetch_api.py --max-calls 300

Only employees (companyUserType 0) get a profile file.

Behaviour:
  * Every response is cached under data/raw/ and never fetched again.
  * Stops at --max-calls, or when Cinode reports fewer than --reserve daily
    requests left (x-daily-requests-left), or on HTTP 429. Rerun the next day
    to resume where it stopped.
  * A 403/404 on one employee's skills is logged to
    data/raw/profile_skills/_skipped.jsonl (id, status, Cinode's error message)
    and the run continues; reruns skip those ids unless --retry-skipped is
    given. Five in a row stops the run. Every skipped profile is printed with
    name and reason, both when it fails and when a rerun passes over it, and
    listed again in the final summary.
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
from collections import Counter
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
SKIP_CODES = {403, 404}  # per-user errors that should not stop the run
SKIPPED = RAW / "profile_skills" / "_skipped.jsonl"


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


class Skip(Exception):
    def __init__(self, code: int, reason: str = ""):
        super().__init__(f"HTTP {code}")
        self.code, self.reason = code, reason


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
                self.calls += 1
                if e.code == 429:
                    raise QuotaStop("HTTP 429 from Cinode (quota or rate limit)") from e
                if e.code in SKIP_CODES:
                    raise Skip(e.code, e.read().decode("utf-8", "replace").strip()[:300]) from e
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


def read_skipped() -> dict[int, dict]:
    """Latest _skipped.jsonl entry per company user id."""
    if not SKIPPED.exists():
        return {}
    entries = (json.loads(line) for line in SKIPPED.read_text(encoding="utf-8").splitlines() if line.strip())
    return {e["company_user_id"]: e for e in entries}


def log_skipped(uid: int, code: int, reason: str) -> None:
    SKIPPED.parent.mkdir(parents=True, exist_ok=True)
    with SKIPPED.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"company_user_id": uid, "status": code, "reason": reason,
                            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                           ensure_ascii=False) + "\n")


def who(u: dict) -> str:
    """Terminal-only label for a user; names are never written to files."""
    name = f"{u.get('firstName') or ''} {u.get('lastName') or ''}".strip()
    return f"{u['companyUserId']} ({name})" if name else str(u["companyUserId"])


def skill_row(s: dict) -> dict:
    """Keep the raw API fields and add the flat ones load_duckdb.py expects."""
    kw = s.get("keyword") or {}
    return {**s,
            "keywordSynonymId": s.get("keywordSynonymId"),  # null in v0.1; loader falls back
            "keywordId": s.get("keywordId") or kw.get("id"),
            "name": s.get("name") or kw.get("masterSynonym"),
            "level": s.get("level")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=300)
    ap.add_argument("--reserve", type=int, default=2000,
                    help="stop when fewer daily requests than this remain")
    ap.add_argument("--retry-skipped", action="store_true",
                    help="retry employees logged in _skipped.jsonl after a 403/404")
    args = ap.parse_args()
    load_env()
    client = Client(args.max_calls, args.reserve)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    skip_log = {} if args.retry_skipped else read_skipped()
    saved, skipped, in_a_row = 0, Counter(), 0
    skipped_now, passed_over = [], []
    stop, employees = None, []

    try:
        users = cached(RAW / "catalog" / "users.json", lambda: client.get("users"))
        employees = [u for u in users if u.get("companyUserType") == EMPLOYEE]
        print(f"{len(employees)} employees of {len(users)} users")
        for u in employees:
            uid = u["companyUserId"]
            out = RAW / "profile_skills" / f"{uid}.json"
            if out.exists():
                continue
            if uid in skip_log:
                e = skip_log[uid]
                passed_over.append((u, e))
                print(f"SKIP {who(u)}: HTTP {e['status']} on {e['at']} (logged; --retry-skipped to try again)")
                continue
            try:
                cached(out, lambda: {"_fetched": now, "company_user_id": uid,
                                     "skills": [skill_row(s) for s in client.get(f"users/{uid}/skills")]})
            except Skip as e:
                log_skipped(uid, e.code, e.reason)
                skipped[e.code] += 1
                skipped_now.append((u, e))
                in_a_row += 1
                print(f"SKIP {who(u)}: HTTP {e.code} {e.reason or ''}".rstrip())
                if in_a_row >= 5:
                    raise QuotaStop(f"{in_a_row} skipped users in a row; check the token's permissions")
                continue
            saved += 1
            in_a_row = 0
    except QuotaStop as e:
        stop = e

    on_disk = len(list((RAW / "profile_skills").glob("*.json")))
    codes = ", ".join(f"{n} x HTTP {c}" for c, n in sorted(skipped.items())) or "none"
    print(f"Saved {saved} this run ({on_disk} of {len(employees)} on disk); skipped {sum(skipped.values())} ({codes});"
          f" {client.calls} calls used; {client.left} daily requests left.")
    if skipped_now:
        print("Skipped this run:")
        for u, e in skipped_now:
            print(f"  {who(u)}: HTTP {e.code} {e.reason or ''}".rstrip())
    if passed_over:
        print(f"Not retried ({len(passed_over)}, logged in {SKIPPED.relative_to(ROOT)}):")
        for u, e in passed_over:
            print(f"  {who(u)}: HTTP {e['status']} {e.get('reason') or ''}".rstrip())
    if stop:
        print(f"Stopped early: {stop}. Rerun later to resume.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
