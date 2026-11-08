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


def test_revise_merges_buckets_and_splits_keywords():
    kws = {k: {"master_synonym": n, "is_verified": False, "users": u}
           for k, n, u in (("1", "Python", 9), ("2", "Python3", 3), ("3", "Mentor", 2), ("4", "Mentoring", 5))}
    state = [{"keyword_id": k, "name": kws[k]["master_synonym"], "bucket_id": b, "bucket_head": kws[k]["master_synonym"]}
             for k, b in (("1", "1"), ("2", "2"), ("3", "3"), ("4", "3"))]
    buckets.revise(state, kws, [{"op": "merge", "a": "2", "b": "1"}, {"op": "split", "keyword_id": "4"}], 50, "test")
    by = {r["keyword_id"]: r for r in state}
    assert by["1"]["bucket_id"] == by["2"]["bucket_id"] == "1"  # lower bucket id survives
    assert by["1"]["bucket_head"] == "Python"  # most used member heads the bucket
    assert by["4"]["bucket_id"] != by["3"]["bucket_id"] and by["4"]["decision"] == "revised"


def test_role_words_map_roles_to_their_discipline():
    assert buckets.role_words("Product Owner") == buckets.role_words("Product Ownership")
    assert buckets.role_words("Mentor") == buckets.role_words("Mentoring")
    assert buckets.role_words("C") != buckets.role_words("C#")


def test_rules_state_the_buyer_test():
    assert "buyer" in buckets.JUDGE_RULES and "Python3" in buckets.JUDGE_RULES
