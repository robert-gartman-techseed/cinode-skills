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
TAX = ROOT / "data" / "taxonomy"  # keyword catalog exports (general taxonomy)
AI = ROOT / "data" / "ai"  # AI hot-topic layer (hot_topics, topic_mapping)
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


def load_profile_skills() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Full per-employee skill lists (fetch_api.py or the connector), once they are fetched.

    The REST API (v0.1) leaves keywordSynonymId null and nests the keyword, so
    synonym_id falls back to keyword.masterSynonymId. Each keyword's names
    (master + synonyms) go to keyword_names, so topics mapped by synonym can be
    matched to keyword ids by name. keywords holds one row per keyword with
    Cinode's verified flag (curated by Cinode) vs. user-created custom skills.
    """
    rows, names, keywords = [], set(), {}
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
            keywords[keyword_id] = {"keyword_id": keyword_id, "master_synonym_id": kw.get("masterSynonymId"),
                                    "master_synonym": kw.get("masterSynonym") or s.get("name"),
                                    "synonyms": sorted(set(kw.get("synonyms", []))), "type": kw.get("type"),
                                    "universal": kw.get("universal"), "verified": kw.get("verified")}
            for n in [kw.get("masterSynonym"), s.get("name"), *kw.get("synonyms", [])]:
                if n:
                    names.add((keyword_id, n))
    return (pd.DataFrame(rows, columns=["company_user_id", "synonym_id", "keyword_id", "name", "level", "fetched_at"]),
            pd.DataFrame(sorted(names), columns=["keyword_id", "name"]),
            pd.DataFrame(list(keywords.values()),
                         columns=["keyword_id", "master_synonym_id", "master_synonym", "synonyms",
                                  "type", "universal", "verified"]))


def main() -> None:
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    con = duckdb.connect(str(DB))

    tags = load_tags()
    tag_types = pd.DataFrame(json.loads((RAW / "catalog" / "tagTypes.json").read_text(encoding="utf-8"))["items"])
    skill_sets, skill_set_skills = load_skill_sets()
    skill_hits, skill_synonyms = load_skill_search()
    profile_skills, keyword_names, keywords = load_profile_skills()
    hot_topics = pd.read_csv(AI / "hot_topics.csv")
    topic_mapping = pd.read_csv(AI / "topic_mapping.csv")

    for name, df in {
        "tags": tags, "tag_types": tag_types, "skill_sets": skill_sets,
        "skill_set_skills": skill_set_skills, "skill_search_hits": skill_hits,
        "skill_synonyms": skill_synonyms, "profile_skills": profile_skills,
        "keyword_names": keyword_names, "keywords": keywords,
        "hot_topics": hot_topics, "topic_mapping": topic_mapping,
    }.items():
        con.register("_df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _df")
        con.unregister("_df")

    # Mapped topic skills (synonym ids) resolved to keyword ids. A keyword counts for a
    # topic only when its own (master) name or id is in that topic's mapping: matching
    # through any synonym let the generic keyword "AI" (which lists "AI in Business
    # Development" as a synonym) inflate topic 29.
    con.execute("""
        CREATE VIEW topic_keywords AS
        WITH m AS (SELECT * FROM topic_mapping WHERE kind = 'skill'),
        kw AS (SELECT DISTINCT keyword_id, synonym_id AS master_id, name FROM profile_skills),
        cand AS (
            SELECT DISTINCT m.topic_id, m.cinode_id AS synonym_id, k.keyword_id, m.match
            FROM m JOIN keyword_names k ON lower(k.name) = lower(m.cinode_name)
            UNION
            SELECT DISTINCT m.topic_id, m.cinode_id, kw.keyword_id, m.match
            FROM m JOIN kw ON kw.master_id = m.cinode_id
        )
        SELECT c.* FROM cand c JOIN kw USING (keyword_id)
        WHERE EXISTS (SELECT 1 FROM m WHERE m.topic_id = c.topic_id
                      AND (m.cinode_id = kw.master_id OR lower(m.cinode_name) = lower(kw.name)))
    """)

    # Best self-assessed level per employee and keyword, from full profiles.
    con.execute("""
        CREATE VIEW employee_skills AS
        SELECT company_user_id, keyword_id, max(level) AS level, 'profile' AS source
        FROM profile_skills GROUP BY ALL
    """)

    # Every keyword seen on a profile, with aggregate use at TechSeed (no per-person rows).
    # verified = curated by Cinode; unverified = custom skill created by some Cinode user.
    con.execute("""
        CREATE VIEW keyword_usage AS
        SELECT k.*, CASE k.type
                   WHEN 0 THEN 'Uncategorized' WHEN 1 THEN 'Industries' WHEN 2 THEN 'Roles'
                   WHEN 3 THEN 'Tools' WHEN 4 THEN 'Techniques' WHEN 5 THEN 'Methods & Processes'
                   WHEN 6 THEN 'Platforms' WHEN 7 THEN 'Products' WHEN 10 THEN 'Certifications'
                   WHEN 11 THEN 'Materials' WHEN 12 THEN 'Specifications & Regulations'
                   WHEN 13 THEN 'Hardware' WHEN 14 THEN 'Operational area and function'
                   WHEN 15 THEN 'Construction' WHEN 16 THEN 'Reports & Investigations'
                   WHEN 17 THEN 'Specialities - Medicine' WHEN 18 THEN 'Standards & Regulations'
                   WHEN 19 THEN 'Accreditation' WHEN 20 THEN 'Soft skills' WHEN 100 THEN 'CustomName'
               END AS type_name,  -- KeywordType enum from the Cinode OpenAPI spec
               count(DISTINCT p.company_user_id) AS techseed_users,
               round(avg(p.level) FILTER (WHERE p.level > 0), 2) AS avg_level,
               max(p.level) AS max_level
        FROM keywords k JOIN profile_skills p USING (keyword_id)
        GROUP BY ALL
    """)

    # Drift: every unverified keyword paired with its most likely verified counterpart.
    # Names are normalised (lowercase, letters/digits/+/# only). Methods, best first:
    #   same_name - main names equal, or the custom main name equals a verified
    #               keyword's main name in another language listed among its synonyms
    #               on both sides (e.g. Swedish/English pairs)
    #   synonym   - the custom main name is listed as a synonym of a verified keyword.
    #               Synonym lists contain user additions (e.g. "C++" under "Programming"),
    #               so treat this as weaker evidence.
    #   similar   - Jaro-Winkler >= 0.93 between main names (5+ characters)
    #   contains  - the custom main name contains a verified main name (6+ characters);
    #               weakest, often a generic word such as "Integration"
    con.execute("""
        CREATE VIEW skill_drift AS
        WITH k AS (SELECT keyword_id, verified,
                          regexp_replace(lower(master_synonym), '[^a-z0-9åäöæøü+#]', '', 'g') AS norm
                   FROM keywords),
        syn AS (SELECT DISTINCT kn.keyword_id, regexp_replace(lower(kn.name), '[^a-z0-9åäöæøü+#]', '', 'g') AS norm
                FROM keyword_names kn),
        u AS (SELECT * FROM k WHERE NOT verified AND norm <> ''),
        v AS (SELECT * FROM k WHERE verified AND norm <> ''),
        cand AS (
            SELECT u.keyword_id AS custom_id, v.keyword_id AS verified_id, 'same_name' AS match_method,
                   1.0 AS similarity, 1 AS prio
            FROM u JOIN v USING (norm)
            UNION ALL
            SELECT u.keyword_id, v.keyword_id, 'same_name', 1.0, 1
            FROM u JOIN syn su ON su.keyword_id = u.keyword_id
                   JOIN v ON v.norm = su.norm
                   JOIN syn sv ON sv.keyword_id = v.keyword_id AND sv.norm = u.norm
            UNION ALL
            SELECT u.keyword_id, sv.keyword_id, 'synonym', 1.0, 2
            FROM u JOIN syn sv ON sv.norm = u.norm JOIN v ON v.keyword_id = sv.keyword_id
            UNION ALL
            SELECT u.keyword_id, v.keyword_id, 'similar', jaro_winkler_similarity(u.norm, v.norm), 3
            FROM u JOIN v ON length(u.norm) >= 5 AND length(v.norm) >= 5
                 AND jaro_winkler_similarity(u.norm, v.norm) >= 0.93
            UNION ALL
            SELECT u.keyword_id, v.keyword_id, 'contains', length(v.norm) / length(u.norm), 4
            FROM u JOIN v ON length(v.norm) >= 6 AND length(u.norm) > length(v.norm) AND contains(u.norm, v.norm)
        ),
        best AS (
            SELECT c.* FROM cand c JOIN keyword_usage v ON v.keyword_id = c.verified_id
            QUALIFY row_number() OVER (PARTITION BY c.custom_id
                                       ORDER BY c.prio, c.similarity DESC, v.techseed_users DESC, v.keyword_id) = 1
        )
        SELECT u.keyword_id AS custom_keyword_id, u.master_synonym AS custom_name,
               u.techseed_users AS custom_users, u.avg_level AS custom_avg_level,
               b.verified_id AS verified_keyword_id, v.master_synonym AS verified_name,
               v.techseed_users AS verified_users, coalesce(b.match_method, 'none') AS match_method,
               round(b.similarity, 3) AS similarity
        FROM keyword_usage u
        LEFT JOIN best b ON b.custom_id = u.keyword_id
        LEFT JOIN keyword_usage v ON v.keyword_id = b.verified_id
        WHERE NOT u.verified
    """)

    # Keyword catalog exports (aggregate only, tracked in git).
    for name, query in {
        "keywords": "FROM keyword_usage ORDER BY keyword_id",
        "verified_keywords": "FROM keyword_usage WHERE verified ORDER BY techseed_users DESC, keyword_id",
        "skill_drift": "FROM skill_drift ORDER BY custom_users DESC, custom_keyword_id",
    }.items():
        df = con.execute(query).df()
        if "synonyms" in df:
            df["synonyms"] = df["synonyms"].map(lambda xs: "; ".join(xs))
        df.to_csv(TAX / f"{name}.csv", index=False)

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
              "profile_skills", "keyword_names", "keywords", "hot_topics", "topic_mapping"]:
        print(f"{t:20} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>6} rows")
    print(f"\nWrote {DB}")


if __name__ == "__main__":
    main()
