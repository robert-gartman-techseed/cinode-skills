"""Offline tests for src/buckets.py: token translation and decision resolution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import buckets  # noqa: E402


def test_words_translates_swedish_tokens():
    assert buckets.words("Datastrategi") == {"datastrategi"}  # one word, no split
    assert "strategy" in buckets.words("Data strategi")
    assert buckets.words("Agentisk utveckling") == buckets.words("Agentic development")
    assert {"machine", "learning"} <= buckets.words("Maskininlärning")


def test_resolve_follows_buckets_and_earlier_keywords():
    state = [{"keyword_id": "1", "bucket_id": "7"}]
    decisions = [
        {"keyword_id": "2", "decision": "synonym", "matched_bucket_id": 7},
        {"keyword_id": "3", "decision": "new"},
        {"keyword_id": "4", "decision": "synonym", "matched_keyword_id": "3"},  # joins a bucket opened in this batch
    ]
    r = buckets.resolve(decisions, state)
    assert r["2"] == "7" and r["3"] == "new:3" and r["4"] == "new:3"
