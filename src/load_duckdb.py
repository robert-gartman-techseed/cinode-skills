"""Load everything under data/ into db/cinode.duckdb.

Raw files are never modified; the database is rebuilt from scratch each run,
so it is always safe to rerun after new raw files arrive.

    uv run src/load_duckdb.py
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
REF = ROOT / "data" / "reference"
DB = ROOT / "db" / "cinode.duckdb"


def load_tags() -> pd.DataFrame:
    rows = []
    for line in (RAW / "catalog" / "tags.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        tag_id, name = line.split("|", 1)
        rows.append({"tag_id": int(tag_id), "name": name})
    return pd.DataFrame(rows)


def load_skill_sets() -> tuple[pd.DataFrame, pd.DataFrame]:
    data = json.loads((RAW / "catalog" / "skillSets.json").read_text(encoding="utf-8"))
    sets, skills = [], []
    for s in data["items"]:
        sets.append({"skill_set_id": s["id"], "name": s["name"], "description": s.get("description")})
        for keyword_id, synonym_id, name, level in s["skills"]:
            skills.append({"skill_set_id": s["id"], "keyword_id": keyword_id,
                           "synonym_id": synonym_id, "name": name, "required_level": level})
    return pd.DataFrame(sets), pd.DataFrame(skills, columns=["skill_set_id", "keyword_id", "synonym_id", "name", "required_level"])


def load_skill_search() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every file in raw/skill_search/ is one batch of search-employees-by-skill calls."""
    hits, synonyms = [], {}
    for path in sorted((RAW / "skill_search").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for q in data["queries"]:
            for syn_id, name in q["syn"].items():
                synonyms[int(syn_id)] = name
            for user_id, syn_id, level, score in q["hits"]:
                hits.append({"source_file": path.name, "query": q["q"], "min_score": q["min"],
                             "company_user_id": user_id, "synonym_id": syn_id,
                             "level": level, "score": score})
    syn_df = pd.DataFrame([{"synonym_id": k, "name": v} for k, v in synonyms.items()])
    return pd.DataFrame(hits), syn_df


def load_profile_skills() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full per-employee skill lists (fetch_api.py or the connector), once they are fetched.

    The REST API (v0.1) leaves keywordSynonymId null and nests the keyword, so
    synonym_id falls back to keyword.masterSynonymId. Each keyword's names
    (master + synonyms) go to keyword_names, so topics mapped by synonym can be
    matched to keyword ids by name.
    """
    rows, names = [], set()
    for path in sorted((RAW / "profile_skills").glob("*.json")) if (RAW / "profile_skills").exists() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        user_id = data["company_user_id"]
        for s in data["skills"]:
            kw = s.get("keyword") or {}
            keyword_id = s.get("keywordId") or kw.get("id")
            rows.append({"company_user_id": user_id,
                         "synonym_id": s.get("keywordSynonymId") or kw.get("masterSynonymId"),
                         "keyword_id": keyword_id, "name": s.get("name"),
                         "level": s.get("level"), "fetched_at": data.get("_fetched")})
            for n in [kw.get("masterSynonym"), s.get("name"), *kw.get("synonyms", [])]:
                if n:
                    names.add((keyword_id, n))
    return (pd.DataFrame(rows, columns=["company_user_id", "synonym_id", "keyword_id", "name", "level", "fetched_at"]),
            pd.DataFrame(sorted(names), columns=["keyword_id", "name"]))


def main() -> None:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    con = duckdb.connect(str(DB))

    tags = load_tags()
    tag_types = pd.DataFrame(json.loads((RAW / "catalog" / "tagTypes.json").read_text(encoding="utf-8"))["items"])
    skill_sets, skill_set_skills = load_skill_sets()
    skill_hits, skill_synonyms = load_skill_search()
    profile_skills, keyword_names = load_profile_skills()
    hot_topics = pd.read_csv(REF / "hot_topics.csv")
    topic_mapping = pd.read_csv(REF / "topic_mapping.csv")

    for name, df in {
        "tags": tags, "tag_types": tag_types, "skill_sets": skill_sets,
        "skill_set_skills": skill_set_skills, "skill_search_hits": skill_hits,
        "skill_synonyms": skill_synonyms, "profile_skills": profile_skills,
        "keyword_names": keyword_names,
        "hot_topics": hot_topics, "topic_mapping": topic_mapping,
    }.items():
        con.register("_df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _df")
        con.unregister("_df")

    # Mapped topic skills (synonym ids) resolved to keyword ids, by synonym name or master id.
    con.execute("""
        CREATE VIEW topic_keywords AS
        SELECT DISTINCT m.topic_id, m.cinode_id AS synonym_id, k.keyword_id, m.match
        FROM topic_mapping m JOIN keyword_names k ON lower(k.name) = lower(m.cinode_name)
        WHERE m.kind = 'skill'
        UNION
        SELECT DISTINCT m.topic_id, m.cinode_id, p.keyword_id, m.match
        FROM topic_mapping m JOIN profile_skills p ON p.synonym_id = m.cinode_id
        WHERE m.kind = 'skill'
    """)

    # Best self-assessed level per employee and keyword, from full profiles.
    con.execute("""
        CREATE VIEW employee_skills AS
        SELECT company_user_id, keyword_id, max(level) AS level, 'profile' AS source
        FROM profile_skills GROUP BY ALL
    """)

    # Per topic: how many employees hold a mapped skill, and at what self-assessed level.
    # Profile holders are matched by keyword; everyone else falls back to search hits.
    con.execute("""
        CREATE VIEW topic_coverage AS
        WITH m AS (SELECT * FROM topic_mapping WHERE kind = 'skill'),
        obs AS (
            SELECT t.topic_id, e.company_user_id, e.level, t.match
            FROM topic_keywords t JOIN employee_skills e USING (keyword_id)
            UNION ALL
            SELECT m.topic_id, h.company_user_id, h.level, m.match
            FROM m JOIN skill_search_hits h ON h.synonym_id = m.cinode_id
            WHERE h.company_user_id NOT IN (SELECT company_user_id FROM profile_skills)
        ),
        per_user AS (
            SELECT topic_id, company_user_id, max(level) AS level, bool_or(match = 'exact') AS has_exact
            FROM obs GROUP BY ALL
        )
        SELECT t.topic_id, t.category, t.topic, t.status,
               count(DISTINCT p.company_user_id) FILTER (WHERE p.has_exact) AS employees_exact,
               count(DISTINCT p.company_user_id) AS employees_exact_or_close,
               count(DISTINCT p.company_user_id) FILTER (WHERE p.level >= 3) AS employees_level_3plus,
               round(avg(p.level) FILTER (WHERE p.level > 0), 1) AS avg_level_rated
        FROM hot_topics t LEFT JOIN per_user p USING (topic_id)
        GROUP BY ALL ORDER BY t.topic_id
    """)
    con.close()

    con = duckdb.connect(str(DB), read_only=True)
    for t in ["tags", "tag_types", "skill_sets", "skill_search_hits", "skill_synonyms",
              "profile_skills", "keyword_names", "hot_topics", "topic_mapping"]:
        print(f"{t:20} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>6} rows")
    print(f"\nWrote {DB}")


if __name__ == "__main__":
    main()
