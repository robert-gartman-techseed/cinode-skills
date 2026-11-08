# Cinode skills: taxonomy and AI hot topics

Local workspace for TechSeed's skill data in Cinode, in two layers:

* **Skill taxonomy** (all skills, domain-neutral): the keyword catalog with
  usage counts, reviewed synonyms, alias buckets (level 1) and buyer-facing
  marketable skills (level 2).
* **AI hot topics** (a layer on top): the sales team's hot AI topics, which
  taxonomy keywords map to them, curated canonical AI skills with definitions,
  and the catalogue page data. It refers to taxonomy keywords by id.

Data is cached as raw files and loaded into DuckDB.

**Personal data:** `data/raw/skill_search/` and `data/raw/profile_skills/`
hold employee ids with self-assessed skill levels. Keep this folder private;
share only aggregated counts.

## Layout

```
data/raw/catalog/        tags.txt, tagTypes.json, skillSets.json
data/raw/skill_search/   search-employees-by-skill results, one file per batch (personal, gitignored)
data/raw/profile_skills/ full skill list per employee, one file per person (personal, gitignored)
data/taxonomy/           general skill taxonomy (aggregate counts only)
  keywords.csv, verified_keywords.csv, skill_drift.csv   keyword catalog (written by load_duckdb.py)
  wikidata_links.csv, review_queue.csv, skill_aliases.csv  synonym hints and reviewed aliases
  skill_buckets.csv      level 1 alias buckets + level 2 skill_id/skill_name per keyword
  marketable_skills.csv  level 2: one row per marketable skill with member buckets and counts
  revisions/             hand-made revise / l2merge op files, as applied
  reports/               custom keywords vs verified keywords
data/ai/                 AI hot-topic layer
  hot_topics.csv, topic_mapping.csv, skill_relations.json
  canonical_skills.csv (definitions), ai_skillset_proposal.csv, canonical_skills_view.json
  reports/ai_verified_keywords.csv
data/external/wikidata/  cached public Wikidata responses (committed, names only)
data/external/techseed_web/ what_we_build.md: the 6 expertise areas from techseed.se
src/load_duckdb.py       rebuilds db/cinode.duckdb from everything above
src/skills.py            taxonomy core: catalog, synonym review, Wikidata, person summary
src/buckets.py           taxonomy: alias buckets and marketable skills
src/ai_topics.py         AI layer: canonical AI skills, AI report, catalogue view
src/fetch_api.py         direct-API fetcher (token from .env)
tests/                   offline tests (uv run pytest)
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
`data/taxonomy/` on every load. Cinode's API only offers
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

## Skill taxonomy

### Keyword synonyms (`src/skills.py`)

Cinode keywords are linked through reviewed aliases (`skill_aliases.csv`);
Wikidata is used only as a hint for which keywords mean the same thing. ESCO,
JobTech and Lightcast were tested and do not cover modern skills well enough.

```
uv run src/skills.py wikidata          # keywords used by 2+ people; --all for the rest, --include CSV for extras
uv run src/skills.py refresh           # writes data/taxonomy/review_queue.csv
#   fill in decision (synonym/narrower/broader/related/none) and, if not
#   suggestion 1, canonical_id; then:
uv run src/skills.py accept            # moves decided rows to skill_aliases.csv
uv run src/skills.py report            # data/taxonomy/reports/*.csv
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
* `report` answers: custom keywords that are synonyms of a verified keyword, and
  custom keywords with no verified match.
* `person` lists every skill on one profile with level and status: `verified`
  (Cinode-verified keyword), `synonym (accepted)` (reviewed alias),
  `synonym (suggested)` (strong unreviewed match to a verified keyword: exact
  name, corroborated Cinode synonym, shared Wikidata item or spelling) or
  `unique` (custom, no verified counterpart), plus duplicates on the same profile
  and, when the AI layer is present, the hot topic. This is personal data: it
  only prints to the terminal or writes to the gitignored `data/out/`.

### Alias buckets, level 1 (`src/buckets.py`)

Robert's algorithm: take the keywords one at a time (verified first, then most
used, so the established name heads each bucket); compare each with the
keywords already examined; if one belongs with it under the merge rule it joins
that bucket, otherwise it opens a new one. Reviewed synonyms from `skill_aliases.csv` (and,
optionally, a reviewed groups file passed with `--groups`, e.g. the AI layer's
`data/ai/topic_mapping.csv`) are seeded first.

Merge rule (the buyer test): skills are a sales tool for consultant buyers, so
two keywords share a bucket when a buyer searching for one would consider a
consultant listing the other to have the same marketable skill. Versions,
editions, spelling and language variants, "X compliance" / "X development" /
"X pipelines" variants and a role with its discipline merge ("Python3" =
"Python", "Mentor" = "Mentoring"). Genuinely different skills stay apart ("C" /
"C#", "TypeScript" / "JavaScript"), and a specific product never merges with a
generic skill or another vendor's product ("Jira" / "Issue tracking", "Power BI"
/ "Tableau"). The full text given to the judges is printed by
`uv run src/buckets.py rules`.

The judging is done by Claude Code subagents in a workflow, one batch of 50 at
a time; `buckets.py` does the deterministic part (order, shortlists, state):

```
uv run src/buckets.py next --scope pilot   # data/work/batch.json (scope: pilot | used2 | all)
#   subagent judges the batch -> data/work/decisions.json
uv run src/buckets.py apply data/work/decisions.json --model sonnet
uv run src/buckets.py stats --show 10
uv run src/buckets.py pairs                # candidate bucket pairs for a missed-merge pass
uv run src/buckets.py revise ops.json      # merge/split buckets: {"ops": [{"op": "merge", "a": kid, "b": kid}]}
```

### Marketable skills, level 2

Level-1 buckets stay as they are (aliases of one skill, nothing lost). Level 2
(`skill_id`, `skill_name` in `skill_buckets.csv`, one row per skill in
`marketable_skills.csv`) groups buckets into the
headline a consultant buyer reads on a CV, merging aggressively: close
neighbours and families (SQL / T-SQL / PL/SQL, HTML & CSS, Cloud / Cloud
Computing, GitLab / GitLab CI), sub-skills under their headline unless buyers
search them separately (Test Automation, Cyber Security, DevOps stay their
own), frameworks under their language (Spring Boot -> Java). Kept apart:
skills a buyer chooses between (C / C++ / C#, Java / JavaScript, AWS / Azure /
GCP, React / Angular, SAP / Dynamics), and a product never stands as a generic
skill (Jira is not Project Management; product variants join their product). Rules: `uv run src/buckets.py rules --level 2`.

```
uv run src/buckets.py l2next --size 100    # unassigned buckets, most used first, with shortlist + all skills
#   subagent judges join/new -> decisions.json
uv run src/buckets.py l2apply decisions.json --batch data/work/l2batch.json
uv run src/buckets.py l2merge ops.json      # consolidate: merge skills, move a bucket, rename
uv run src/buckets.py l2stats --show 20
uv run src/buckets.py l2export            # data/taxonomy/marketable_skills.csv
```

State is `data/taxonomy/skill_buckets.csv` (keyword names, counts, bucket,
decision, reason, model, skill), so a stopped run resumes. Hand-made revisions
(`revise` / `l2merge` op files) are kept in `data/taxonomy/revisions/`. Limitation: each keyword is
judged only against a shortlist of up to 15 candidate buckets found by cheap
signals (normalised name, spelling, sv/en token overlap, abbreviation, shared
Wikidata item, same `--groups` group); a synonym with none of these in common can be
missed.

## AI hot topics (`src/ai_topics.py`, `data/ai/`)

The sales team's hot AI topics (`hot_topics.csv`) and the curated canonical AI
skills (`canonical_skills.csv`, seeded from the hot topics, ids `T01`...). Every
AI-related or hot-topic skill needs a one-sentence `definition` (about 25 words,
plain English, so an employee can tell whether they have the skill;
vendor-neutral unless the skill is a product); a test checks this. Taxonomy
keywords are mapped to topics in `topic_mapping.csv` by Cinode id.

```
uv run src/ai_topics.py seed      # canonical_skills.csv from hot_topics.csv (only if missing)
uv run src/ai_topics.py report    # data/ai/reports/ai_verified_keywords.csv
uv run src/ai_topics.py view      # data/ai/canonical_skills_view.json (catalogue page data)
uv run src/skills.py wikidata --include data/ai/topic_mapping.csv   # also link mapped 1-person keywords
```

* `topic_mapping.csv` has a `relation` per mapped keyword: `synonym` (a semantic
  alias: the same concept in any wording, e.g. "LLM" = "Large Language Models";
  test both ways: someone listing either keyword has exactly the skill as
  defined, same scope), `product` (a specific product, model, vendor or tool,
  e.g. ChatGPT, Azure AI Foundry), `narrower` (a sub-skill) or `related`
  (overlapping but different). Only `synonym` is an alias; the rest still counts
  toward topic coverage. The hand-reviewed table is `skill_relations.json`.
* The AI layer can feed its reviewed synonyms into the taxonomy's alias buckets
  with `uv run src/buckets.py --groups data/ai/topic_mapping.csv next ...`.
* In the taxonomy, AI products are ordinary marketable skills (ChatGPT, OpenAI API
  & Platform, Azure AI Foundry, Microsoft Copilot, AI-assisted Development, AI
  Agents & Agentic AI, AI Governance & EU AI Act); the placements are recorded in
  `data/taxonomy/revisions/2026-11-08_l2_product_placements.json`.
* In DuckDB, `topic_coverage` counts employees per topic and level.
