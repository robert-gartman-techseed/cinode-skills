"""Skill taxonomy core: keyword catalog, synonym review and Wikidata hints (AI-neutral).

    uv run src/skills.py wikidata [--all] [--include CSV]  # link keywords to Wikidata items (public API, cached)
    uv run src/skills.py refresh            # build data/taxonomy/review_queue.csv
    uv run src/skills.py accept [--by NAME] # move reviewed queue rows into skill_aliases.csv
    uv run src/skills.py report             # custom synonyms of verified / custom unmatched
    uv run src/skills.py person <id|name>   # one person's skills: verified / synonym / unique / duplicate
    uv run src/skills.py person --all       # team summary, counts only

The AI hot-topic layer (canonical AI skills, topic mapping, catalogue view) is
src/ai_topics.py; it reads this taxonomy, never the other way round (except the
optional hot-topic column in `person`).

`person` reads the gitignored profile files; its output is personal data and
only goes to the terminal or the gitignored data/out/.

Input is data/taxonomy/keywords.csv (written by load_duckdb.py from the fetched
profiles; keyword names and aggregate counts only). Everything this script writes
is keyword-level reference data and is meant to be committed; no per-employee rows.

Candidate methods, strongest first:
  alias      the name normalises like a keyword that already has a reviewed alias
  exact      same normalised name (case, punctuation, plurals, "Name (ABBR)" forms)
  cinode     listed in the other keyword's Cinode synonym list, corroborated (listed both
             ways, or the names are similar: token_set_ratio >= 70)
  wikidata   both keywords link to the same Wikidata item
  fuzzy      rapidfuzz ratio >= 93 on normalised names (spelling variants)
  cinode_list  listed in a Cinode synonym list without corroboration. Users can add
             synonyms, so these lists are noisy (e.g. "JavaScript" under "Java").
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAX = ROOT / "data" / "taxonomy"  # general skill taxonomy (all skills)
AI = ROOT / "data" / "ai"  # AI hot-topic layer on top of the taxonomy (src/ai_topics.py)
CACHE = ROOT / "data" / "external" / "wikidata"
KEYWORDS = TAX / "keywords.csv"
QUEUE = TAX / "review_queue.csv"
ALIASES = TAX / "skill_aliases.csv"
LINKS = TAX / "wikidata_links.csv"
REPORTS = TAX / "reports"
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "out"  # gitignored: per-person output
STRONG = {"alias", "exact", "cinode", "wikidata", "fuzzy"}  # methods that count as a suggested synonym

API = "https://www.wikidata.org/w/api.php"
UA = "cinode-skills-analysis/0.1 (TechSeed internal skill taxonomy; https://www.techseed.se)"
LANGS = ("en", "sv", "nb")
METHOD_RANK = {"alias": 0, "exact": 1, "cinode": 2, "wikidata": 3, "fuzzy": 4, "cinode_list": 5}
DECISIONS = {"synonym", "product", "narrower", "broader", "related", "none"}
# Relation of a keyword to a canonical skill. Only "synonym" is an alias: a semantic alias, the same
# concept in any wording ("LLM" = "Large Language Models"), tested both ways: someone listing either
# keyword has exactly the skill as defined (same scope). Sub-skills are "narrower", supersets and
# neighbouring skills "related", and products, models, vendors and tools are never aliases ("product").
RELATIONS = ("synonym", "product", "narrower", "related")
FUZZY_MIN = 93

# Wikidata classes. An item is kept when it, or a class it is an instance/subclass of
# (up to CLASS_HOPS levels up), is in ALLOW, and none of its own P31 classes is in BLOCK.
ALLOW = {
    "Q7397": "software", "Q166142": "application software", "Q40056": "computer program",
    "Q9143": "programming language", "Q271680": "software framework", "Q188860": "software library",
    "Q9135": "operating system", "Q8513": "database", "Q176165": "database management system",
    "Q189210": "web application", "Q1153191": "online service", "Q193424": "web service",
    "Q235557": "file format", "Q8366": "algorithm", "Q188267": "programming paradigm",
    "Q2695280": "technique", "Q1799072": "method", "Q3505845": "methodology",
    "Q2267705": "field of study", "Q11862829": "academic discipline", "Q1047113": "specialty",
    "Q4671286": "academic major", "Q2465832": "branch of science", "Q336": "science",
    "Q317623": "technical standard", "Q15836568": "computer network protocol", "Q132364": "communication protocol",
    "Q12737077": "occupation", "Q28640": "profession", "Q205961": "skill",
    "Q11016": "technology", "Q1378235": "software development process", "Q11660": "artificial intelligence",
    "Q2539": "machine learning", "Q1440693": "model", "Q17155032": "software category",
    "Q1371279": "management system", "Q2061186": "business process", "Q2424752": "product",
}
COMPANY = {"Q4830453", "Q783794", "Q891723", "Q6881511"}  # business, company, public company, enterprise
BLOCK = {
    "Q13442814": "scholarly article", "Q5": "human", "Q571": "book", "Q7725634": "literary work",
    "Q47461344": "written work", "Q1266946": "thesis", "Q732577": "publication", "Q4167410": "disambiguation page",
    "Q189533": "academic degree", "Q163727": "master's degree", "Q11424": "film", "Q5398426": "TV series",
    "Q482994": "album", "Q7366": "song", "Q134556": "single", "Q55488": "railway station", "Q515": "city",
    "Q486972": "human settlement", "Q6256": "country", "Q3624078": "sovereign state", "Q4022": "river",
    "Q101352": "family name", "Q202444": "given name", "Q12308941": "male given name", "Q11879590": "female given name",
    "Q4167836": "Wikimedia category", "Q17442446": "Wikimedia internal item", "Q16521": "taxon",
    "Q1002697": "periodical", "Q41298": "magazine", "Q5633421": "scientific journal",
}
AI_ROOTS = {"Q11660", "Q2539"}  # artificial intelligence, machine learning
CLASS_HOPS = 3


# ---------- normalising ----------

def basic(s: str) -> str:
    """Lowercase, keep letters/digits/+/# (incl. Nordic letters), drop everything else."""
    return re.sub(r"[^a-z0-9åäöæøü+#]", "", s.lower())


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is", "ys")):
        return word[:-1]
    return word


def _abbreviates(a: str, b: str) -> bool:
    """Is one part an abbreviation of the other ("Large Language Models" / "LLM",
    "MLOps" / "Machine Learning Operations")? Not "RAG" / "LLM"."""
    for long, short in ((a, b), (b, a)):
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", long)).lower()
        letters = re.sub(r"[^a-z0-9]", "", short.lower()).removesuffix("s")
        if len(initials) >= 2 and len(letters) >= 2 and (letters == initials or letters.startswith(initials)
                                                         or initials.startswith(letters)):
            return True
    return False


def forms(name: str) -> set[str]:
    """Normalised forms of a skill name: whole name, and for "Name (ABBR)" both parts.

    >>> sorted(forms("Large Language Models (LLM)"))
    ['largelanguagemodel', 'largelanguagemodelllm', 'llm']
    """
    out = set()
    parts = [name]
    m = re.fullmatch(r"\s*(.+?)\s*\(([^()]+)\)\s*", name)
    if m and _abbreviates(m.group(1), m.group(2)):
        parts += [m.group(1), m.group(2)]
    for p in parts:
        words = re.findall(r"[A-Za-z0-9åäöæøüÅÄÖÆØÜ+#]+", p)
        f = "".join(_singular(w.lower()) for w in words)
        if f:
            out.add(f)
    return out


# ---------- io ----------

def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_keywords() -> dict[str, dict]:
    if not KEYWORDS.exists():
        sys.exit(f"{KEYWORDS.relative_to(ROOT)} missing; run `uv run src/load_duckdb.py` first")
    kws = {}
    for r in read_csv(KEYWORDS):
        r["users"] = int(r["techseed_users"] or 0)
        r["is_verified"] = r["verified"] == "True"
        r["syn_list"] = [s for s in r["synonyms"].split("; ") if s]
        kws[r["keyword_id"]] = r
    return kws


# ---------- wikidata ----------

_session = None


def wd_get(params: dict) -> dict:
    """GET the Wikidata API, caching a trimmed response under data/external/wikidata/.

    Uses one keep-alive session: opening a new connection per request led to
    frequent dropped connections through the proxy.
    """
    global _session
    import requests

    query = urllib.parse.urlencode(sorted(params.items()))
    path = CACHE / params["action"] / f"{hashlib.sha1(query.encode()).hexdigest()}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = UA
    waits = (5, 15)  # back off on throttling or dropped connections
    for attempt in range(len(waits) + 1):
        try:
            r = _session.get(API, params=params, timeout=30)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < len(waits):
                time.sleep(int(r.headers.get("Retry-After") or waits[attempt]))
                continue
            r.raise_for_status()
            data = r.json()
            break
        except requests.ConnectionError:
            if attempt == len(waits):
                raise
            time.sleep(waits[attempt])
    data = trim(params["action"], data)
    data["_query"] = query
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    time.sleep(0.2)  # at most ~5 requests per second
    return data


def trim(action: str, data: dict) -> dict:
    """Keep only the fields this script uses, so the committed cache stays small."""
    if action == "wbsearchentities":
        return {"search": [{"id": h["id"], "label": h.get("label", ""), "description": h.get("description", ""),
                            "match": h.get("match", {})} for h in data.get("search", [])]}
    ents = {}
    for qid, e in data.get("entities", {}).items():
        claims = e.get("claims", {})
        ids = lambda p: sorted({c["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
                                for c in claims.get(p, []) if c.get("mainsnak", {}).get("datavalue")} - {None})
        ents[qid] = {"labels": {l: v["value"] for l, v in e.get("labels", {}).items()},
                     "aliases": {l: [a["value"] for a in v] for l, v in e.get("aliases", {}).items()},
                     "P31": ids("P31"), "P279": ids("P279")}
    return {"entities": ents}


def wd_entities(qids: set[str]) -> dict[str, dict]:
    out, todo = {}, sorted(qids)
    for i in range(0, len(todo), 50):
        out.update(wd_get({"action": "wbgetentities", "ids": "|".join(todo[i:i + 50]),
                           "props": "labels|aliases|claims", "languages": "|".join(LANGS),
                           "format": "json"})["entities"])
    return out


def ancestors(qid: str, ents: dict[str, dict], hops: int = CLASS_HOPS) -> set[str]:
    """The item's P31 and P279 classes and their P279 superclasses, `hops` levels up."""
    e = ents.get(qid, {})
    level, seen = set(e.get("P31", [])) | set(e.get("P279", [])), set()
    for _ in range(hops):
        seen |= level
        level = {s for c in level for s in ents.get(c, {}).get("P279", [])} - seen
    return seen | level


def classify(qid: str, ents: dict[str, dict]) -> tuple[bool, str, str]:
    """(keep, confidence, reason) for a candidate item, using cached entity data only."""
    e = ents.get(qid, {})
    own = set(e.get("P31", []))
    blocked = own & BLOCK.keys()
    if blocked:
        return False, "", "blocked: " + ", ".join(BLOCK[b] for b in sorted(blocked))
    if qid in ALLOW:
        return True, "high", "is " + ALLOW[qid]
    hits = (ancestors(qid, ents) | own) & ALLOW.keys()
    if hits:
        return True, "high", "under " + ", ".join(ALLOW[h] for h in sorted(hits))
    if own & COMPANY:
        return True, "low", "company (product name?)"
    return False, "", "no skill-like class"


def is_ai(qid: str, ents: dict[str, dict]) -> bool:
    return bool(({qid} | ancestors(qid, ents)) & AI_ROOTS)


def cmd_wikidata(args) -> None:
    kws = load_keywords()
    extra = {r.get("cinode_id") or r.get("master_synonym_id") for r in read_csv(Path(args.include))} if args.include else set()
    scope = [k for k in kws.values() if args.all or k["users"] >= 2 or k["master_synonym_id"] in extra]
    print(f"Searching Wikidata for {len(scope)} keywords ({'all' if args.all else 'used by 2+ people or included'})")

    found: dict[str, list[tuple[str, str, str, int]]] = {}
    failed: set[str] = set()
    for n, k in enumerate(scope, 1):
        wanted, hits = forms(k["master_synonym"]), []
        for lang in LANGS:
            try:
                res = wd_get({"action": "wbsearchentities", "search": k["master_synonym"][:250], "language": lang,
                              "uselang": lang, "type": "item", "limit": "10", "format": "json"})
            except Exception as e:  # network trouble for one term; the rest of the run continues
                print(f"  lookup failed for {k['master_synonym']!r} ({lang}): {type(e).__name__}; skipped, rerun to retry",
                      flush=True)
                failed.add(k["keyword_id"])
                break
            for rank, h in enumerate(res["search"]):
                if forms(h["match"].get("text", "")) & wanted:
                    hits.append((h["id"], h["match"].get("type", ""), lang, rank))
            if hits:  # Swedish/Norwegian only when English found nothing
                break
        found[k["keyword_id"]] = hits
        if n % 200 == 0:
            print(f"  {n} searched", flush=True)

    cand = {q for hits in found.values() for q, *_ in hits}
    ents = wd_entities(cand)
    for _ in range(CLASS_HOPS):  # fetch the class hierarchy level by level
        need = {c for q in list(ents) for c in ents[q].get("P31", []) + ents[q].get("P279", [])} - ents.keys()
        if not need:
            break
        ents.update(wd_entities(need))

    rows = {r["keyword_id"]: r for r in read_csv(LINKS)}
    for kid, hits in found.items():
        if kid in failed and not hits:
            continue  # leave any earlier row; retried on the next run
        best = None
        for qid, mtype, lang, rank in sorted(set(hits), key=lambda h: (h[1] != "label", LANGS.index(h[2]), h[3])):
            keep, conf, reason = classify(qid, ents)
            if keep and (best is None or (conf == "high" and best[2] != "high")):
                best = (qid, mtype, conf, reason)
        row = {"keyword_id": kid, "keyword_name": kws[kid]["master_synonym"], "qid": "", "label_en": "",
               "label_sv": "", "aliases": "", "match_type": "", "confidence": "", "ai_related": "",
               "reason": "no matching Wikidata label or alias" if not hits else "only non-skill items matched"}
        if best:
            qid, mtype, conf, reason = best
            e = ents[qid]
            if mtype == "alias" and conf == "high":
                conf = "medium"
            row.update(qid=qid, label_en=e["labels"].get("en", ""), label_sv=e["labels"].get("sv", ""),
                       aliases="; ".join(e["aliases"].get("en", [])[:8] + e["aliases"].get("sv", [])[:8]),
                       match_type=mtype, confidence=conf, ai_related=is_ai(qid, ents), reason=reason)
        rows[kid] = row
    write_csv(LINKS, sorted(rows.values(), key=lambda r: int(r["keyword_id"])),
              ["keyword_id", "keyword_name", "qid", "label_en", "label_sv", "aliases", "match_type",
               "confidence", "ai_related", "reason"])
    linked = [r for r in rows.values() if r["qid"]]
    groups = defaultdict(list)
    for r in linked:
        groups[r["qid"]].append(r["keyword_name"])
    multi = {q: v for q, v in groups.items() if len(v) > 1}
    print(f"Linked {len(linked)} of {len(rows)} keywords to Wikidata; {len(multi)} synonym groups "
          f"(2+ keywords sharing an item). Wrote {LINKS.relative_to(ROOT)}")


# ---------- refresh ----------

def better(a: dict, b: dict) -> bool:
    """Is keyword a the better canonical target than b? Verified first, then usage, then older id."""
    return (a["is_verified"], a["users"], -int(a["keyword_id"])) > (b["is_verified"], b["users"], -int(b["keyword_id"]))


def candidates(kws: dict[str, dict], aliases: list[dict], links: list[dict]) -> dict[str, dict[str, tuple]]:
    """For each keyword: {target_id: (method, score)} keeping the strongest method per target."""
    out: dict[str, dict[str, tuple]] = defaultdict(dict)

    def add(src: str, dst: str, method: str, score: float) -> None:
        if src == dst or not better(kws[dst], kws[src]):
            return
        old = out[src].get(dst)
        if old is None or (METHOD_RANK[method], -score) < (METHOD_RANK[old[0]], -old[1]):
            out[src][dst] = (method, score)

    by_form = defaultdict(set)
    for kid, k in kws.items():
        for f in forms(k["master_synonym"]):
            by_form[f].add(kid)

    for a in aliases:  # reviewed aliases: names that normalise like the alias point to its canonical
        if a["relation"] == "synonym" and a["keyword_id"] in kws and a["canonical_id"] in kws:
            for f in forms(kws[a["keyword_id"]]["master_synonym"]):
                for kid in by_form[f]:
                    add(kid, a["canonical_id"], "alias", 100)
    for ids in by_form.values():
        for a in ids:
            for b in ids:
                add(a, b, "exact", 100)
    from rapidfuzz import fuzz, process

    listed = defaultdict(set)  # keyword id -> ids of keywords whose name is in its synonym list
    for kid, k in kws.items():
        for s in k["syn_list"]:
            for f in forms(s):
                listed[kid] |= by_form.get(f, set()) - {kid}
    for kid, others in listed.items():  # Cinode synonym lists
        for other in others:
            sim = fuzz.token_set_ratio(kws[kid]["master_synonym"].lower(), kws[other]["master_synonym"].lower())
            method = "cinode" if kid in listed.get(other, ()) or sim >= 70 else "cinode_list"
            add(other, kid, method, sim)
            add(kid, other, method, sim)
    by_qid = defaultdict(list)
    for r in links:
        if r["qid"] and r["keyword_id"] in kws:
            by_qid[r["qid"]].append(r["keyword_id"])
    for ids in by_qid.values():
        for a in ids:
            for b in ids:
                add(a, b, "wikidata", 100)

    # spelling variants
    ids = [kid for kid, k in kws.items() if len(min(forms(k["master_synonym"]) or {""}, key=len)) >= 5]
    names = [max(forms(kws[i]["master_synonym"]), key=len) for i in ids]
    scores = process.cdist(names, names, scorer=fuzz.ratio, score_cutoff=FUZZY_MIN, dtype="uint8", workers=-1)
    for i, j in zip(*scores.nonzero()):
        # a spelling variant, not one name inside the other ("AI Transformation" is not "Transformation")
        if i != j and names[i] not in names[j] and names[j] not in names[i]:
            add(ids[i], ids[j], "fuzzy", float(scores[i, j]))
    return out


def cmd_refresh(args) -> None:
    kws = load_keywords()
    aliases = read_csv(ALIASES)
    decided = {a["keyword_id"] for a in aliases}
    old = {r["keyword_id"]: r for r in read_csv(QUEUE)}
    cands = candidates(kws, aliases, read_csv(LINKS))

    rows = []
    for kid, targets in cands.items():
        if kid in decided or not targets:
            continue
        k = kws[kid]
        top = sorted(targets.items(), key=lambda t: (METHOD_RANK[t[1][0]], -t[1][1], not kws[t[0]]["is_verified"],
                                                     -kws[t[0]]["users"]))[:3]
        row = {"keyword_id": kid, "keyword_name": k["master_synonym"], "verified": k["is_verified"],
               "techseed_users": k["users"], "decision": "", "canonical_id": "", "notes": ""}
        for n, (tid, (method, score)) in enumerate(top, 1):
            t = kws[tid]
            row.update({f"s{n}_id": tid, f"s{n}_name": t["master_synonym"], f"s{n}_verified": t["is_verified"],
                        f"s{n}_users": t["users"], f"s{n}_method": method, f"s{n}_score": round(score)})
        if kid in old:  # keep decisions a reviewer already typed in but has not accepted yet
            row.update({c: old[kid].get(c, "") for c in ("decision", "canonical_id", "notes")})
        rows.append(row)
    rows.sort(key=lambda r: (-r["techseed_users"], int(r["keyword_id"])))
    fields = ["keyword_id", "keyword_name", "verified", "techseed_users", "decision", "canonical_id", "notes"]
    fields += [f"s{n}_{c}" for n in (1, 2, 3) for c in ("id", "name", "verified", "users", "method", "score")]
    write_csv(QUEUE, rows, fields)
    by_method = defaultdict(int)
    for r in rows:
        by_method[r["s1_method"]] += 1
    print(f"Review queue: {len(rows)} keywords ({dict(sorted(by_method.items(), key=lambda m: METHOD_RANK[m[0]]))}"
          f" by strongest suggestion); {len(decided)} already decided. Wrote {QUEUE.relative_to(ROOT)}")


# ---------- accept ----------

def cmd_accept(args) -> None:
    kws = load_keywords()
    queue, aliases = read_csv(QUEUE), read_csv(ALIASES)
    by = args.by or subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
    keep, accepted, errors = [], 0, []
    for r in queue:
        decision = r.get("decision", "").strip().lower()
        if not decision:
            keep.append(r)
            continue
        if decision not in DECISIONS:
            errors.append(f"{r['keyword_id']}: unknown decision {decision!r}")
            keep.append(r)
            continue
        canonical = r.get("canonical_id", "").strip() or (r.get("s1_id", "") if decision != "none" else "")
        if decision != "none" and canonical not in kws:
            errors.append(f"{r['keyword_id']}: canonical_id {canonical!r} is not a known keyword id")
            keep.append(r)
            continue
        sugg = next((n for n in (1, 2, 3) if r.get(f"s{n}_id") == canonical), None)
        aliases.append({"keyword_id": r["keyword_id"], "canonical_id": canonical, "relation": decision,
                        "method": r.get(f"s{sugg}_method", "manual") if sugg else "manual",
                        "confidence": r.get(f"s{sugg}_score", "") if sugg else "",
                        "reviewed_by": by, "reviewed_at": date.today().isoformat()})
        accepted += 1
    write_csv(ALIASES, aliases, ["keyword_id", "canonical_id", "relation", "method", "confidence",
                                 "reviewed_by", "reviewed_at"])
    write_csv(QUEUE, keep, list(queue[0].keys()) if queue else [])
    print(f"Accepted {accepted} decisions into {ALIASES.relative_to(ROOT)}; {len(keep)} left in the queue.")
    for e in errors:
        print("  skipped " + e)


# ---------- report ----------

def cmd_report(args) -> None:
    """Custom keywords that are synonyms of a verified keyword, and custom keywords with no verified match."""
    kws = load_keywords()
    links = {r["keyword_id"]: r for r in read_csv(LINKS) if r["qid"]}
    out_b, out_c = custom_reports(kws, links, read_csv(ALIASES))
    for name, rows in {"custom_synonyms_of_verified": out_b, "custom_without_verified": out_c}.items():
        rows.sort(key=lambda r: (-r["techseed_users"], int(r["keyword_id"])))
        write_csv(REPORTS / f"{name}.csv", rows, list(rows[0].keys()) if rows else ["keyword_id"])
        print(f"{name:30} {len(rows):>5} keywords")
    print(f"Wrote {REPORTS.relative_to(ROOT)}/")


def custom_reports(kws: dict[str, dict], links: dict[str, dict], aliases: list[dict]) -> tuple[list, list]:
    verified_by_qid = defaultdict(list)
    for kid, r in links.items():
        if kws.get(kid, {}).get("is_verified"):
            verified_by_qid[r["qid"]].append(kid)
    out_b, matched = [], set()
    for a in aliases:
        if a["relation"] == "synonym" and a["keyword_id"] in kws and kws.get(a["canonical_id"], {}).get("is_verified"):
            out_b.append({"keyword_id": a["keyword_id"], "name": kws[a["keyword_id"]]["master_synonym"],
                          "techseed_users": kws[a["keyword_id"]]["users"], "verified_id": a["canonical_id"],
                          "verified_name": kws[a["canonical_id"]]["master_synonym"], "via": "reviewed alias"})
            matched.add(a["keyword_id"])
    for kid, r in links.items():
        k = kws.get(kid)
        if k and not k["is_verified"] and kid not in matched and verified_by_qid.get(r["qid"]):
            v = max((kws[i] for i in verified_by_qid[r["qid"]]), key=lambda x: x["users"])
            out_b.append({"keyword_id": kid, "name": k["master_synonym"], "techseed_users": k["users"],
                          "verified_id": v["keyword_id"], "verified_name": v["master_synonym"],
                          "via": f"wikidata {r['qid']}"})
            matched.add(kid)
    out_c = [{"keyword_id": kid, "name": k["master_synonym"], "techseed_users": k["users"],
              "qid": links.get(kid, {}).get("qid", "")}
             for kid, k in kws.items() if not k["is_verified"] and kid not in matched]
    return out_b, out_c


def optional_topics() -> tuple[dict[str, str], dict[str, str]]:
    """Hot-topic labels for `person`, from the AI layer when it is present (else none)."""
    try:
        import ai_topics
    except ImportError:
        return {}, {}
    return ai_topics.topic_index()


# ---------- person ----------

def profile_status(skills_on_profile: list[dict], kws: dict[str, dict], cands: dict[str, dict[str, tuple]],
                   aliases: list[dict], topics: tuple[dict, dict]) -> list[dict]:
    """Classify one person's skills.

    skills_on_profile: [{"keyword_id", "level"}]. Status per skill:
      verified             the keyword is Cinode-verified
      synonym (accepted)   custom, reviewed alias (skill_aliases.csv) to another keyword
      synonym (suggested)  custom, strong unreviewed suggestion to a verified keyword
      unique               custom with no verified counterpart
    duplicate is set when two skills on the profile resolve to the same target.
    """
    accepted = {a["keyword_id"]: a for a in aliases if a["relation"] == "synonym"}
    by_id, by_name = topics
    rows = []
    for s in skills_on_profile:
        kid = str(s["keyword_id"])
        k = kws.get(kid, {"master_synonym": f"keyword {kid}", "is_verified": False, "master_synonym_id": ""})
        strong = sorted(((t, m) for t, m in cands.get(kid, {}).items() if m[0] in STRONG),
                        key=lambda tm: (not kws[tm[0]]["is_verified"], METHOD_RANK[tm[1][0]], -tm[1][1]))
        if k["is_verified"]:
            status, target, evidence = "verified", kid, ""
        elif kid in accepted:
            target = accepted[kid]["canonical_id"]
            status, evidence = "synonym (accepted)", f"reviewed alias ({accepted[kid]['reviewed_by']})"
        elif strong and kws[strong[0][0]]["is_verified"]:
            target, (method, score) = strong[0]
            status, evidence = "synonym (suggested)", f"{method} {round(score)}"
        else:
            status, evidence = "unique", ""
            target = strong[0][0] if strong else kid  # still used to spot duplicates between custom skills
        t = kws.get(target, {})
        topic = (by_id.get(k["master_synonym_id"]) or by_name.get(k["master_synonym"].lower())
                 or by_id.get(t.get("master_synonym_id", "")) or by_name.get(t.get("master_synonym", "").lower(), ""))
        rows.append({"keyword_id": kid, "skill": k["master_synonym"], "level": s.get("level"),
                     "verified": k["is_verified"], "status": status,
                     "target_id": "" if target == kid else target,
                     "target": "" if target == kid else t.get("master_synonym", target),
                     "evidence": evidence, "topic": topic, "_key": target})
    seen = defaultdict(list)
    for r in rows:
        seen[r["_key"]].append(r["skill"])
    for r in rows:
        others = [n for n in seen[r.pop("_key")] if n != r["skill"]]
        r["duplicate"] = "duplicate of " + ", ".join(others) if others else ""
    order = ["verified", "synonym (accepted)", "synonym (suggested)", "unique"]
    return sorted(rows, key=lambda r: (order.index(r["status"]), -(r["level"] or 0), r["skill"].lower()))


def summarise(rows: list[dict]) -> dict:
    out = {st: sum(r["status"] == st for r in rows)
           for st in ("verified", "synonym (accepted)", "synonym (suggested)", "unique")}
    out["duplicates"] = sum(bool(r["duplicate"]) for r in rows)
    out["skills"] = len(rows)
    return out


def load_profile(uid: str) -> list[dict]:
    data = json.loads((RAW / "profile_skills" / f"{uid}.json").read_text(encoding="utf-8"))
    return [{"keyword_id": s.get("keywordId") or (s.get("keyword") or {}).get("id"), "level": s.get("level")}
            for s in data["skills"]]


def cmd_person(args) -> None:
    t0 = time.time()
    kws = load_keywords()
    aliases = read_csv(ALIASES)
    cands = candidates(kws, aliases, read_csv(LINKS))
    topics = optional_topics()
    have = {p.stem for p in (RAW / "profile_skills").glob("*.json")}

    if args.all:
        rows = []
        for uid in sorted(have, key=int):
            rows.append({"company_user_id": uid, **summarise(profile_status(load_profile(uid), kws, cands, aliases, topics))})
        write_csv(OUT / "team_summary.csv", rows, list(rows[0].keys()))
        print(f"Wrote {len(rows)} people to {(OUT / 'team_summary.csv').relative_to(ROOT)} (counts only)")
        return

    users = {str(u["companyUserId"]): u for u in json.loads((RAW / "catalog" / "users.json").read_text(encoding="utf-8"))}
    q = args.who.strip()
    matches = [q] if q in users else [uid for uid, u in users.items()
                                      if q.lower() in f"{u.get('firstName', '')} {u.get('lastName', '')}".lower()]
    if len(matches) != 1:
        sys.exit("No match." if not matches else "Several matches, use the id:\n" + "\n".join(
            f"  {m}  {users[m].get('firstName', '')} {users[m].get('lastName', '')}" for m in matches))
    uid = matches[0]
    if uid not in have:
        sys.exit(f"No fetched profile for {uid} (skipped or not fetched yet).")
    rows = profile_status(load_profile(uid), kws, cands, aliases, topics)
    u = users[uid]
    print(f"{u.get('firstName', '')} {u.get('lastName', '')} ({uid}): {len(rows)} skills\n")
    w = max([len(r["skill"]) for r in rows] + [5])
    for r in rows:
        extra = " ".join(x for x in (f"-> {r['target']}" if r["target"] else "", f"[{r['evidence']}]" if r["evidence"] else "",
                                     r["topic"], f"({r['duplicate']})" if r["duplicate"] else "") if x)
        print(f"  {r['skill'][:60]:<{min(w, 60)}}  {r['level'] if r['level'] is not None else '-':>1}  {r['status']:<19}  {extra}")
    sm = summarise(rows)
    print(f"\n{sm['verified']} verified, {sm['synonym (accepted)']} synonym (accepted), "
          f"{sm['synonym (suggested)']} synonym (suggested), {sm['unique']} unique; "
          f"{sm['duplicates']} on the profile are duplicates. ({time.time() - t0:.1f}s)")
    if args.csv:
        path = OUT / f"person_{uid}.csv"
        write_csv(path, rows, list(rows[0].keys()))
        print(f"Wrote {path.relative_to(ROOT)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wikidata", help="link keywords to Wikidata items (public API, cached)")
    w.add_argument("--all", action="store_true", help="every keyword, not only those used by 2+ people")
    w.add_argument("--include", help="CSV with cinode_id (master synonym id) of extra keywords to link, "
                                      "e.g. data/ai/topic_mapping.csv")
    sub.add_parser("refresh", help="build the review queue")
    a = sub.add_parser("accept", help="move reviewed queue rows into skill_aliases.csv")
    a.add_argument("--by", help="reviewer name (default: git user.name)")
    sub.add_parser("report", help="write the two taxonomy reports")
    p = sub.add_parser("person", help="one person's skills: verified / synonym / unique / duplicate")
    p.add_argument("who", nargs="?", default="", help="company user id or part of the name")
    p.add_argument("--all", action="store_true", help="team summary (counts per person) to data/out/")
    p.add_argument("--csv", action="store_true", help="also write data/out/person_<id>.csv")
    args = ap.parse_args()
    if args.cmd == "person" and not (args.all or args.who):
        ap.error("person needs an id, a name or --all")
    {"wikidata": cmd_wikidata, "refresh": cmd_refresh, "accept": cmd_accept, "report": cmd_report,
     "person": cmd_person}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
