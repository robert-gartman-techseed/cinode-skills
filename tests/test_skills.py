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
