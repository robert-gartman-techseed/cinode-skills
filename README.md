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
                         keywords.csv, verified_keywords.csv, skill_drift.csv
                         (generated keyword catalog, aggregate counts only)
src/load_duckdb.py       rebuilds db/cinode.duckdb from everything above
src/skills.py            skill synonym tooling (wikidata / refresh / accept / report)
data/external/wikidata/  cached public Wikidata responses (committed, names only)
tests/                   offline tests (uv run pytest)
src/fetch_api.py         direct-API fetcher (token from .env)
db/cinode.duckdb         generated; safe to delete
```

## Use

Uses [uv](https://docs.astral.sh/uv/) (`brew install uv`); dependencies are in
`pyproject.toml` / `uv.lock` and `uv run` creates `.venv` on first use.

```
uv run src/fetch_api.py --max-calls 300   # optional, see below
uv run src/load_duckdb.py
uv run python -c "import duckdb; print(duckdb.connect('db/cinode.duckdb').sql('from topic_coverage'))"
```

Tables: `tags`, `tag_types`, `skill_sets`, `skill_set_skills`,
`skill_search_hits`, `skill_synonyms`, `profile_skills`, `keyword_names`,
`hot_topics`, `topic_mapping`. Views: `topic_keywords` (mapped synonyms
resolved to keyword ids by name), `employee_skills` (best level per person and
keyword from profiles), `topic_coverage` (employees per topic and level; search
hits fill in for people without a fetched profile).
`keyword_usage` (every keyword seen with Cinode's `verified` flag and TechSeed
user counts) and `skill_drift` (each unverified/custom keyword with its closest
verified keyword: same_name > synonym > similar > contains) are exported to
`data/reference/` on every load. Cinode's API only offers
`GET /keywords/search/{term}` (no listing or paging), so the catalog covers the
keywords TechSeed employees use, not Cinode's full verified list.

The REST API returns `keywordSynonymId: null` on profile skills, so profiles
are matched by keyword id. A keyword counts for a topic only when its own
(master) name or id is in `topic_mapping.csv`; sharing a synonym is not enough
(the generic keyword "AI" lists "AI in Business Development" as a synonym).

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
  `uv run src/fetch_api.py --max-calls 300`. Uses `/v0.1/companies/{id}/users`
  (filtered to `companyUserType` 0) and `/users/{companyUserId}/skills`.
  Needs a machine that can reach api.cinode.com; some proxies truncate large
  skill responses, so it asks for gzip and retries cut-short reads. A 403/404
  for one employee is logged to `profile_skills/_skipped.jsonl` and skipped
  on reruns (`--retry-skipped` to try again).

Then rerun `src/load_duckdb.py`.

## Skill synonyms (`src/skills.py`)

Our own curated list is the reference (`data/reference/canonical_skills.csv`,
seeded from the 30 hot topics, ids `T01`-`T30`). Every AI-related or hot-topic
skill needs a one-sentence `definition` (about 25 words, plain English, so an
employee can tell whether they have the skill; vendor-neutral unless the skill
is a product). Other canonical skills may leave it empty; a test checks this. Cinode keywords are linked to it through
reviewed aliases. Wikidata is used only as a hint for which keywords mean the
same thing. ESCO, JobTech and Lightcast were tested and do not cover modern
AI skills well enough.

```
uv run src/skills.py wikidata          # keywords used by 2+ people + mapped ones; --all for the rest
uv run src/skills.py refresh           # writes data/reference/review_queue.csv
#   fill in decision (synonym/narrower/broader/related/none) and, if not
#   suggestion 1, canonical_id; then:
uv run src/skills.py accept            # moves decided rows to skill_aliases.csv
uv run src/skills.py report            # data/reference/reports/*.csv
uv run src/skills.py person 152386     # one person (id or part of name), --csv to save
uv run src/skills.py person --all      # team summary, counts per person
uv run pytest
```

* `wikidata` searches en/sv/nb labels and aliases (max ~5 requests/s, every
  response cached under `data/external/wikidata/`, so reruns are free) and keeps
  only items whose class chain looks like a skill (software, method, field of
  study, standard, occupation, ...). Papers, books, people, degrees (e.g.
  "Master of Laws" for LLM) are dropped. Output: `wikidata_links.csv`; two or
  more keywords with the same `qid` form a synonym group.
* `refresh` suggests up to three better targets per keyword (verified first),
  strongest method first: reviewed alias, exact normalised name, Cinode synonym
  list, shared Wikidata item, spelling (rapidfuzz >= 93). Decided keywords are
  never re-queued, and decisions typed into the queue survive a refresh.
* `report` answers: AI-related verified keywords, custom keywords that are
  synonyms of a verified keyword, and custom keywords with no verified match.
* `topic_mapping.csv` has a `relation` per mapped keyword: `synonym` (a semantic
  alias: the same concept in any wording, e.g. "LLM" = "Large Language Models";
  test both ways: someone listing either keyword has exactly the skill as
  defined, same scope), `product` (a specific
  product, model, vendor or tool, e.g. ChatGPT, Azure AI Foundry), `narrower`
  (a sub-skill) or `related` (overlapping but different). Only `synonym` is an
  alias; the rest still counts toward topic coverage. The hand-reviewed table
  is `skill_relations.json`. Semantic aliases cannot be found by spelling or
  Wikidata alone ("Agent Design" vs "Agentic AI" needs judgement), so the
  automatic suggestions only catch the surface cases; the rest is review. `uv run src/skills.py view` rebuilds the catalogue
  page data `canonical_skills_view.json`.
* `person` lists every skill on one profile with level and status: `verified`
  (Cinode-verified keyword), `synonym (accepted)` (reviewed alias),
  `synonym (suggested)` (strong unreviewed match to a verified keyword: exact
  name, corroborated Cinode synonym, shared Wikidata item or spelling) or
  `unique` (custom, no verified counterpart), plus duplicates on the same profile
  and the hot topic. This is personal data: it only prints to the terminal or
  writes to the gitignored `data/out/`.
