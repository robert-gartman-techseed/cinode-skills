"""Skill synonym tooling: our own curated list is the reference, Wikidata helps find synonyms.

    uv run src/skills.py wikidata [--all]   # link keywords to Wikidata items (public API, cached)
    uv run src/skills.py refresh            # build data/reference/review_queue.csv
    uv run src/skills.py accept [--by NAME] # move reviewed queue rows into skill_aliases.csv
    uv run src/skills.py report             # AI-related verified / custom synonyms / custom unmatched

Input is data/reference/keywords.csv (written by load_duckdb.py from the fetched
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
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "reference"
CACHE = ROOT / "data" / "external" / "wikidata"
KEYWORDS = REF / "keywords.csv"
QUEUE = REF / "review_queue.csv"
ALIASES = REF / "skill_aliases.csv"
LINKS = REF / "wikidata_links.csv"
CANONICAL = REF / "canonical_skills.csv"
REPORTS = REF / "reports"

API = "https://www.wikidata.org/w/api.php"
UA = "cinode-skills-analysis/0.1 (TechSeed internal skill taxonomy; https://www.techseed.se)"
LANGS = ("en", "sv", "nb")
METHOD_RANK = {"alias": 0, "exact": 1, "cinode": 2, "wikidata": 3, "fuzzy": 4, "cinode_list": 5}
DECISIONS = {"synonym", "narrower", "broader", "related", "none"}
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


def forms(name: str) -> set[str]:
    """Normalised forms of a skill name: whole name, and for "Name (ABBR)" both parts.

    >>> sorted(forms("Large Language Models (LLM)"))
    ['largelanguagemodel', 'largelanguagemodelllm', 'llm']
    """
    out = set()
    parts = [name]
    m = re.fullmatch(r"\s*(.+?)\s*\(([^()]+)\)\s*", name)
    if m:
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

def wd_get(params: dict) -> dict:
    """GET the Wikidata API, caching a trimmed response under data/external/wikidata/."""
    query = urllib.parse.urlencode(sorted(params.items()))
    path = CACHE / params["action"] / f"{hashlib.sha1(query.encode()).hexdigest()}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": UA})
    waits = (10, 30, 60, 120)  # back off on throttling or dropped connections
    for attempt in range(len(waits) + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == len(waits):
                raise
            time.sleep(int(e.headers.get("Retry-After") or waits[attempt]))
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == len(waits):
                raise
            time.sleep(waits[attempt])
    data = trim(params["action"], data)
    data["_query"] = query
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    time.sleep(0.3)  # about 3 requests per second, under Wikidata's ~5/s guidance
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
    mapped = {r["cinode_id"] for r in read_csv(REF / "topic_mapping.csv") if r["kind"] == "skill"}
    scope = [k for k in kws.values() if args.all or k["users"] >= 2 or k["master_synonym_id"] in mapped]
    print(f"Searching Wikidata for {len(scope)} keywords ({'all' if args.all else 'used by 2+ people or mapped'})")

    found: dict[str, list[tuple[str, str, str, int]]] = {}
    for n, k in enumerate(scope, 1):
        wanted, hits = forms(k["master_synonym"]), []
        for lang in LANGS:
            res = wd_get({"action": "wbsearchentities", "search": k["master_synonym"][:250], "language": lang,
                          "uselang": lang, "type": "item", "limit": "10", "format": "json"})
            for rank, h in enumerate(res["search"]):
                if forms(h["match"].get("text", "")) & wanted:
                    hits.append((h["id"], h["match"].get("type", ""), lang, rank))
            if hits:  # Swedish/Norwegian only when English found nothing
                break
        found[k["keyword_id"]] = hits
        if n % 200 == 0:
            print(f"  {n} searched")

    cand = {q for hits in found.values() for q, *_ in hits}
    ents = wd_entities(cand)
    for _ in range(CLASS_HOPS):  # fetch the class hierarchy level by level
        need = {c for q in list(ents) for c in ents[q].get("P31", []) + ents[q].get("P279", [])} - ents.keys()
        if not need:
            break
        ents.update(wd_entities(need))

    rows = {r["keyword_id"]: r for r in read_csv(LINKS)}
    for kid, hits in found.items():
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


# ---------- canonical list ----------

def seed_canonical(kws: dict[str, dict]) -> None:
    """Seed canonical_skills.csv from the 30 hot topics (only when the file does not exist)."""
    if CANONICAL.exists():
        return
    by_syn = {k["master_synonym_id"]: k for k in kws.values()}
    mapping = read_csv(REF / "topic_mapping.csv")
    rows = []
    for t in read_csv(REF / "hot_topics.csv"):
        ks = [by_syn[m["cinode_id"]] for m in mapping
              if m["topic_id"] == t["topic_id"] and m["kind"] == "skill" and m["match"] == "exact"
              and m["cinode_id"] in by_syn]
        best = max(ks, key=lambda k: (k["is_verified"], k["users"]), default=None)
        rows.append({"id": f"T{int(t['topic_id']):02d}", "name": t["topic"], "definition": "",
                     "domain": t["category"], "ai_related": t["topic_id"] not in {"15", "22"},
                     "preferred_keyword_id": best["keyword_id"] if best else "", "wikidata_qid": ""})
    write_csv(CANONICAL, rows, ["id", "name", "definition", "domain", "ai_related",
                                "preferred_keyword_id", "wikidata_qid"])
    print(f"Seeded {CANONICAL.relative_to(ROOT)} with {len(rows)} skills from hot_topics.csv")


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
        if i != j:
            add(ids[i], ids[j], "fuzzy", float(scores[i, j]))
    return out


def cmd_refresh(args) -> None:
    kws = load_keywords()
    seed_canonical(kws)
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
    kws = load_keywords()
    links = {r["keyword_id"]: r for r in read_csv(LINKS) if r["qid"]}
    aliases = read_csv(ALIASES)
    canon = read_csv(CANONICAL)
    mapping = read_csv(REF / "topic_mapping.csv")
    ai_topics = {c["id"] for c in canon if c["ai_related"] == "True"}
    syn_to_kw = {k["master_synonym_id"]: kid for kid, k in kws.items()}
    ai_kw = {syn_to_kw[m["cinode_id"]]: f"topic T{int(m['topic_id']):02d}" for m in mapping
             if m["kind"] == "skill" and f"T{int(m['topic_id']):02d}" in ai_topics and m["cinode_id"] in syn_to_kw}
    for kid, r in links.items():
        if r["ai_related"] == "True":
            ai_kw.setdefault(kid, f"wikidata {r['qid']}")

    out_a = [{"keyword_id": kid, "name": k["master_synonym"], "techseed_users": k["users"],
              "ai_source": ai_kw[kid], "qid": links.get(kid, {}).get("qid", "")}
             for kid, k in kws.items() if k["is_verified"] and kid in ai_kw]

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
              "qid": links.get(kid, {}).get("qid", ""), "ai_related": kid in ai_kw}
             for kid, k in kws.items() if not k["is_verified"] and kid not in matched]

    for name, rows in {"ai_verified_keywords": out_a, "custom_synonyms_of_verified": out_b,
                       "custom_without_verified": out_c}.items():
        rows.sort(key=lambda r: (-r["techseed_users"], int(r["keyword_id"])))
        write_csv(REPORTS / f"{name}.csv", rows, list(rows[0].keys()) if rows else ["keyword_id"])
        print(f"{name:30} {len(rows):>5} keywords")
    print(f"Wrote {REPORTS.relative_to(ROOT)}/")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wikidata", help="link keywords to Wikidata items (public API, cached)")
    w.add_argument("--all", action="store_true", help="every keyword, not only those used by 2+ people or mapped")
    sub.add_parser("refresh", help="build the review queue")
    a = sub.add_parser("accept", help="move reviewed queue rows into skill_aliases.csv")
    a.add_argument("--by", help="reviewer name (default: git user.name)")
    sub.add_parser("report", help="write the three reports")
    args = ap.parse_args()
    {"wikidata": cmd_wikidata, "refresh": cmd_refresh, "accept": cmd_accept, "report": cmd_report}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
