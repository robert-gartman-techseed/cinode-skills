# Cinode AI skills analysis

Local workspace for comparing the sales team's 30 "hot" AI topics with what
exists in Cinode (skills and tags) and, later, with what employees have on
their profiles. Data is cached as raw files and loaded into DuckDB.

**Personal data:** `data/raw/skill_search/` and `data/raw/profile_skills/`
hold employee ids with self-assessed skill levels. Keep this folder private;
share only aggregated counts.

## Layout

```
data/raw/catalog/        tags.txt (476 tags), tagTypes.json (4), skillSets.json (6)
data/raw/skill_search/   search-employees-by-skill results, one file per batch
data/raw/profile_skills/ (later) full skill list per employee, one file per person
data/reference/          hot_topics.csv (30 topics + status), topic_mapping.csv
src/load_duckdb.py       rebuilds db/cinode.duckdb from everything above
src/fetch_api.py         direct-API fetcher (token from .env)
db/cinode.duckdb         generated; safe to delete
```

## Use

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/load_duckdb.py
.venv/bin/python -c "import duckdb; print(duckdb.connect('db/cinode.duckdb').sql('from topic_coverage'))"
```

Tables: `tags`, `tag_types`, `skill_sets`, `skill_set_skills`,
`skill_search_hits`, `skill_synonyms`, `profile_skills`, `hot_topics`,
`topic_mapping`. Views: `employee_skills` (best available observation per
person and skill), `topic_coverage` (employees per topic and level).

## Skills vs tags in Cinode

* **Skills** (keyword + synonym id) sit on employee profiles with a 0-5 level.
  This is what "select and self-assess" needs. Level 0 usually means "not rated".
* **Tags** are free labels on customers, projects, employees, candidates.
  They have no level. The 4 tag types (Industries, Delivery Model, Dynamics 365,
  Power Platform) group a few of them.
* **Skill sets** bundle skills with a target level. An empty `AI Skillset`
  (id 9575) already exists; it is the natural home for the curated AI list.

## Fetching more data (quota-aware)

Cinode allows 10,000 API calls per day for the whole company, and an unknown
caller has been using most of it. So:

1. Every fetched response is stored in `data/raw/` and never fetched again.
2. Fetch in batches of at most ~250 calls per day; stop on any quota / 429 error
   and resume the next day.
3. Read-only calls only.

Two ways to fetch:

* **Via the Cinode connector (works today):** ask Claude in this project to
  "fetch the next batch of employee skill profiles into cinode-skills".
  It calls `fetch userProfile:{id}:skills` per employee and writes
  `data/raw/profile_skills/{companyUserId}.json` as
  `{"_fetched", "company_user_id", "skills": [{keywordSynonymId, keywordId, name, level}]}`,
  skipping ids already on disk. About 241 calls for all employees, plus one
  `search type:employees` page per 50 people for the id list.
* **Direct API (`src/fetch_api.py`):** faster and scriptable. Put the company
  integration token in a gitignored `.env` as `CINODE_API_TOKEN=...` (used
  directly as a Bearer token; `CINODE_COMPANY_ID` defaults to 4041) and run
  `python src/fetch_api.py --max-calls 300`. Uses `/v0.1/companies/{id}/users`
  (filtered to `companyUserType` 0) and `/users/{companyUserId}/skills`.
  Needs a machine that can reach api.cinode.com; some proxies truncate large
  skill responses, so it asks for gzip and retries cut-short reads.

Then rerun `src/load_duckdb.py`.
