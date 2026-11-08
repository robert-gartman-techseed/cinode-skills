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


def _l2_state():
    rows = [("1", "SQL", "1"), ("2", "T-SQL", "2"), ("3", "PL/SQL", "3"), ("4", "C#", "4")]
    return [{"keyword_id": k, "name": n, "users": 1, "bucket_id": b, "bucket_head": n, "skill_id": "", "skill_name": ""}
            for k, n, b in rows]


def test_l2_apply_creates_and_joins_marketable_skills():
    state = _l2_state()
    joined, created = buckets.l2_apply(state, [
        {"bucket_id": 1, "decision": "new", "new_key": "n1", "skill_name": "SQL"},
        {"bucket_id": 2, "decision": "join", "new_key": "n1"},  # joins a skill created in this batch
        {"bucket_id": 4, "decision": "new", "skill_name": "C#"},
    ])
    assert (joined, created) == (1, 2)
    buckets.l2_apply(state, [{"bucket_id": 3, "decision": "join", "skill_id": state[0]["skill_id"]}])
    by = {r["name"]: r["skill_id"] for r in state}
    assert by["SQL"] == by["T-SQL"] == by["PL/SQL"] != by["C#"]


def test_l2_merge_consolidates_and_renames():
    state = _l2_state()
    buckets.l2_apply(state, [{"bucket_id": 1, "decision": "new", "skill_name": "SQL"},
                             {"bucket_id": 2, "decision": "new", "skill_name": "Transact-SQL"}])
    buckets.l2_merge(state, [{"keep": state[0]["skill_id"], "drop": state[1]["skill_id"], "name": "SQL"}])
    assert state[0]["skill_id"] == state[1]["skill_id"] and state[1]["skill_name"] == "SQL"


def test_level2_rules_keep_buyer_choices_apart():
    assert "C#" in buckets.JUDGE_RULES_L2 and "AWS / Azure / GCP" in buckets.JUDGE_RULES_L2


def test_l2_merge_moves_a_bucket_to_a_new_skill():
    state = _l2_state()
    buckets.l2_apply(state, [{"bucket_id": 1, "decision": "new", "new_key": "n", "skill_name": "SQL"},
                             {"bucket_id": 2, "decision": "join", "new_key": "n"}])
    buckets.l2_merge(state, [{"move_bucket": 2, "to_name": "T-SQL"}])
    assert state[1]["skill_name"] == "T-SQL" and state[0]["skill_id"] != state[1]["skill_id"]
