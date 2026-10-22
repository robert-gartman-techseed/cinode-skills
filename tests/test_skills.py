"""Offline tests for src/skills.py: name normalising and the Wikidata class filter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import skills  # noqa: E402


def test_forms_case_punctuation_plural():
    assert skills.forms("API-integration") == skills.forms("API integration") == {"apiintegration"}
    assert skills.forms("Large Language Models") == skills.forms("large language model")
    assert skills.forms("CAN bus") == skills.forms("CANbus")


def test_forms_acronym_in_brackets():
    f = skills.forms("Large Language Models (LLM)")
    assert {"llm", "largelanguagemodel"} <= f
    assert skills.forms("LLMs") == {"llm"}
    assert "mlop" in skills.forms("MLOps (Machine Learning Operations)") or "mlops" in skills.forms("MLOps")


def test_forms_keeps_short_words_and_symbols():
    assert skills.forms("AWS") == {"aws"}  # no plural stripping on short words
    assert skills.forms("C#") == {"c#"}
    assert skills.forms("C++") == {"c++"}
    assert skills.forms("Process") == {"process"}  # "ss" is not a plural
    assert skills.forms("Stor språkmodell") == {"storspråkmodell"}


ENTS = {
    # large language model: instance of language model -> subclass of machine learning model
    "Q115305900": {"P31": ["Q3621696"], "P279": []},
    "Q3621696": {"P31": [], "P279": ["Q1440693"]},  # statistical/ML model -> model
    "Q1440693": {"P31": [], "P279": []},
    # Master of Laws: an academic degree
    "Q754848": {"P31": ["Q189533"], "P279": []},
    # a scholarly article about MLOps
    "Q112063412": {"P31": ["Q13442814"], "P279": []},
    # MLOps: subclass of a practice under machine learning
    "Q60753505": {"P31": ["Q1799072"], "P279": ["Q2539"]},
    # a company
    "Q1": {"P31": ["Q4830453"], "P279": []},
    # something unrelated
    "Q2": {"P31": ["Q999"], "P279": []},
    "Q999": {"P31": [], "P279": []},
}


def test_classify_keeps_skills_and_drops_noise():
    assert skills.classify("Q115305900", ENTS)[0] is True
    assert skills.classify("Q60753505", ENTS)[:2] == (True, "high")
    keep, _, reason = skills.classify("Q754848", ENTS)
    assert keep is False and "academic degree" in reason
    assert skills.classify("Q112063412", ENTS)[0] is False
    assert skills.classify("Q1", ENTS)[:2] == (True, "low")
    assert skills.classify("Q2", ENTS)[0] is False


def test_is_ai():
    assert skills.is_ai("Q60753505", ENTS)  # subclass of machine learning
    assert not skills.is_ai("Q754848", ENTS)


def test_ancestors_stops_after_hops():
    chain = {"A": {"P31": ["B"], "P279": []}, "B": {"P279": ["C"]}, "C": {"P279": ["D"]},
             "D": {"P279": ["E"]}, "E": {"P279": ["F"]}}
    assert skills.ancestors("A", chain, hops=2) == {"B", "C", "D"}
    assert "F" not in skills.ancestors("A", chain, hops=3)


def test_ai_and_hot_topic_skills_have_definitions():
    """Definitions are required for AI-related and hot-topic (T..) canonical skills only."""
    rows = skills.read_csv(skills.CANONICAL)
    assert rows, "canonical_skills.csv is missing"
    missing = [r["id"] for r in rows
               if (r["ai_related"] == "True" or r["id"].startswith("T")) and not r["definition"].strip()]
    assert not missing, f"no definition for {missing}"
    assert all(len(r["definition"].split()) <= 30 for r in rows), "keep definitions to one short sentence"


def _kw(kid, name, verified, users=1, syn_id=None):
    return {"keyword_id": kid, "master_synonym": name, "master_synonym_id": syn_id or f"s{kid}",
            "is_verified": verified, "users": users, "syn_list": []}


def test_profile_status_synthetic_person():
    kws = {k["keyword_id"]: k for k in [
        _kw("1", "Generativ AI", True, 5, "107"), _kw("2", "GenAI", False, 2), _kw("3", "Banana", False, 1),
        _kw("4", "Projekt management", False, 8), _kw("5", "Project Management", True, 100),
        _kw("6", "Kubernetes", False, 20), _kw("7", "Process Improvment", True, 1), _kw("8", "Process Improvement", False, 25)]}
    cands = {"2": {"1": ("wikidata", 100)}, "6": {"5": ("cinode_list", 10)}, "8": {"7": ("fuzzy", 97)}}
    aliases = [{"keyword_id": "4", "canonical_id": "5", "relation": "synonym", "reviewed_by": "tester"}]
    topics = ({"107": "T01"}, {})
    person = [{"keyword_id": k, "level": 3} for k in ("1", "2", "3", "4", "6", "8")]
    rows = {r["skill"]: r for r in skills.profile_status(person, kws, cands, aliases, topics)}

    assert rows["Generativ AI"]["status"] == "verified" and rows["Generativ AI"]["topic"] == "T01"
    assert rows["GenAI"]["status"] == "synonym (suggested)" and rows["GenAI"]["target"] == "Generativ AI"
    assert rows["GenAI"]["topic"] == "T01"  # topic inherited from the target
    assert rows["GenAI"]["duplicate"] and rows["Generativ AI"]["duplicate"]  # both on one profile
    assert rows["Projekt management"]["status"] == "synonym (accepted)"
    assert rows["Kubernetes"]["status"] == "unique"  # weak cinode_list hint does not count
    assert rows["Banana"]["status"] == "unique" and not rows["Banana"]["duplicate"]
    assert rows["Process Improvement"]["status"] == "synonym (suggested)"
    s = skills.summarise(list(rows.values()))
    assert (s["verified"], s["synonym (accepted)"], s["synonym (suggested)"], s["unique"], s["duplicates"]) == (1, 1, 2, 2, 2)


def test_profile_status_unknown_keyword_is_unique():
    rows = skills.profile_status([{"keyword_id": 99, "level": None}], {}, {}, [], ({}, {}))
    assert rows[0]["status"] == "unique" and rows[0]["skill"] == "keyword 99"


def test_bracket_split_only_for_real_abbreviations():
    assert "llm" not in skills.forms("RAG (LLM)")
    assert "cms" in skills.forms("Content Management System (CMS)")
    assert "mlop" in skills.forms("MLOps (Machine Learning Operations)")
    assert "machinelearningoperation" in skills.forms("MLOps (Machine Learning Operations)")


def test_topic_mapping_relations():
    """Every mapped skill has a relation; products and models are never synonyms."""
    rows = [r for r in skills.read_csv(skills.REF / "topic_mapping.csv") if r["kind"] == "skill"]
    assert rows and all(r["relation"] in skills.RELATIONS for r in rows)
    rel = {(r["topic_id"], r["cinode_name"]): r["relation"] for r in rows}
    assert rel[("19", "ChatGPT 4")] == "product"
    assert rel[("18", "Azure AI Foundry")] == "product"
    assert rel[("23", "GitHub Copilot")] == "product"
    assert rel[("5", "RAG")] == "synonym"
    assert rel[("1", "Generativ AI")] == "synonym"


def test_profile_status_shows_topic_relation():
    kws = {"1": _kw("1", "ChatGPT", True, 7, "95")}
    rows = skills.profile_status([{"keyword_id": "1", "level": 3}], kws, {}, [], ({"95": "T19 product"}, {}))
    assert rows[0]["topic"] == "T19 product"
