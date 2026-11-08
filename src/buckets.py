"""Semantic synonym buckets: one keyword at a time, judged against earlier buckets.

Algorithm (Robert's): take the keywords in a fixed order; for each, compare it with the
keywords already examined; if one is a semantic synonym, put the keyword in that bucket,
otherwise open a new bucket. The judging is done by a subagent per batch (Claude Code
workflow); this script does everything deterministic around it:

    uv run src/buckets.py next --scope pilot --size 50 --out data/work/batch.json
    #   subagent reads the batch, decides, writes data/work/decisions.json
    uv run src/buckets.py apply data/work/decisions.json
    uv run src/buckets.py stats

State lives in data/reference/skill_buckets.csv (keyword names and counts only), so a
stopped run continues where it left off.

Order: verified keywords first, then by number of TechSeed users, then name, so the most
established name becomes each bucket's head. Reviewed synonyms (topic_mapping.csv
relation=synonym, skill_aliases.csv) are seeded as buckets before the first batch.

Limitation: each keyword is judged only against a shortlist of up to 15 candidate buckets
(normalised name, spelling, token overlap incl. a small sv/en word list, abbreviation,
shared Wikidata item, same hot topic). A synonym with nothing in common on any of those
signals ("Datavetenskap" / "Computer science" without a Wikidata link) can be missed.

Merge rule (buyer test, see JUDGE_RULES): skills are a sales tool for consultant buyers, so
two keywords share a bucket when a buyer searching for one would consider a consultant
listing the other to have the same marketable skill. Versions, editions, spelling and
language variants, "X compliance" / "X development" / "X pipelines" variants and a role
with its discipline merge ("Python3" = "Python", "Mentor" = "Mentoring"). Genuinely
different skills stay apart ("C" / "C#", "TypeScript" / "JavaScript"), and a specific
product never merges with a generic skill or another vendor's product ("ChatGPT" /
"OpenAI API", "Azure AI Foundry" / "Copilot Studio").

Revisions after a run (undo a merge, merge two buckets) go through `revise`; `pairs`
lists candidate bucket pairs (near-identical normalised names, shared Wikidata item, role
vs discipline) for a cheap missed-merge pass.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skills  # noqa: E402

ROOT = skills.ROOT
REF = skills.REF
STATE = REF / "skill_buckets.csv"
FIELDS = ["keyword_id", "name", "verified", "users", "bucket_id", "bucket_head", "decision",
          "matched_keyword", "reason", "batch", "model"]
SHORTLIST = 15

JUDGE_RULES = """\
Skill keywords are a sales tool: consultant buyers search them to find consultants. Two
keywords belong in the same bucket when a buyer searching for one would consider a consultant
listing the other to have the same marketable skill (the buyer test).

Merge:
- spelling, abbreviation, inflection and language variants ("Kvalitetskontroll" = "Quality Control")
- versions and editions ("Python3" = "Python", "CSS3" = "CSS", "PostgreSQL 9.5" = "PostgreSQL")
- "X compliance", "X development", "X pipelines", "X knowledge" variants of X ("GDPR Compliance" = "GDPR")
- a role and its discipline ("Mentor" = "Mentoring", "Product Owner" = "Product Ownership")
- product renames and vendor-prefixed names ("Azure AD" = "Microsoft Entra ID", "Logic Apps" = "Azure Logic Apps")

Keep apart:
- genuinely different skills a buyer would distinguish ("C" / "C#", "TypeScript" / "JavaScript",
  "Machine learning" / "Deep learning", "Scrum" / "Agile", "Project Manager" / "Linux")
- a specific product, model, vendor or tool vs a generic skill ("ChatGPT" / "Generative AI",
  "Jira" / "Issue tracking") or vs another vendor's product ("ChatGPT" / "OpenAI API",
  "Azure AI Foundry" / "Copilot Studio")
- a letters-only match: "ARM" (processor) is not "AI Risk Management"; read the Cinode synonyms.

When in doubt, keep apart.
"""
PILOT_EXTRA = 300

# Swedish/Norwegian -> English words common in skill names, for token overlap only.
SV_EN = {
    "utveckling": "development", "utvikling": "development", "strategi": "strategy", "styrning": "governance",
    "maskininlärning": "machine learning", "maskinlæring": "machine learning", "säkerhet": "security",
    "sikkerhet": "security", "hantering": "management", "ledning": "management", "arkitektur": "architecture",
    "automatisering": "automation", "analys": "analysis", "analyse": "analysis", "kvalitet": "quality",
    "förvaltning": "management", "projektledning": "project management", "systemutveckling": "system development",
    "datalagring": "data storage", "molntjänster": "cloud computing", "databaser": "databases",
    "programmering": "programming", "test": "testing", "testning": "testing", "kravhantering": "requirements management",
    "förändringsledning": "change management", "verksamhetsutveckling": "business development",
    "språkmodell": "language model", "språkmodeller": "language models", "agenter": "agents", "agentisk": "agentic",
    "generativ": "generative", "förordningen": "act", "dataskydd": "data protection", "informationssäkerhet": "information security",
}


def words(name: str) -> set[str]:
    toks = re.findall(r"[a-z0-9åäöæøü+#]+", name.lower())
    out = set()
    for t in toks:
        out.update(SV_EN.get(t, t).split())
    return {skills._singular(w) for w in out if len(w) > 1}


def read_state() -> list[dict]:
    return skills.read_csv(STATE)


def write_state(rows: list[dict]) -> None:
    skills.write_csv(STATE, rows, FIELDS)


def order_key(k: dict) -> tuple:
    return (not k["is_verified"], -k["users"], k["master_synonym"].lower())


def scope_keywords(kws: dict[str, dict], scope: str) -> list[dict]:
    mapped_ids = set()
    by_syn = {k["master_synonym_id"]: kid for kid, k in kws.items()}
    by_name = defaultdict(list)
    for kid, k in kws.items():
        by_name[k["master_synonym"].lower()].append(kid)
    for m in skills.read_csv(REF / "topic_mapping.csv"):
        if m["kind"] == "skill":
            mapped_ids.update(([by_syn[m["cinode_id"]]] if m["cinode_id"] in by_syn else [])
                              + by_name.get(m["cinode_name"].lower(), []))
    if scope == "all":
        chosen = list(kws.values())
    elif scope == "used2":
        chosen = [k for kid, k in kws.items() if k["users"] >= 2 or kid in mapped_ids]
    else:  # pilot: every AI-mapped keyword plus the next most-used ones
        rest = sorted((k for kid, k in kws.items() if kid not in mapped_ids), key=lambda k: (-k["users"], order_key(k)))
        chosen = [kws[i] for i in mapped_ids] + rest[:PILOT_EXTRA]
    return sorted(chosen, key=order_key)


class Signals:
    """Cheap, offline similarity between two keywords, used only to build shortlists."""

    def __init__(self, kws: dict[str, dict]):
        self.kws = kws
        self.forms = {kid: skills.forms(k["master_synonym"]) for kid, k in kws.items()}
        self.words = {kid: words(k["master_synonym"]) for kid, k in kws.items()}
        self.qid = {r["keyword_id"]: r["qid"] for r in skills.read_csv(skills.LINKS) if r["qid"]}
        self.topics = defaultdict(set)
        by_syn = {k["master_synonym_id"]: kid for kid, k in kws.items()}
        for m in skills.read_csv(REF / "topic_mapping.csv"):
            if m["kind"] == "skill" and m["cinode_id"] in by_syn:
                self.topics[by_syn[m["cinode_id"]]].add(m["topic_id"])

    def score(self, a: str, b: str) -> int:
        if self.forms[a] & self.forms[b]:
            return 100
        na, nb = self.kws[a]["master_synonym"], self.kws[b]["master_synonym"]
        s = 0
        if skills._abbreviates(na, nb):
            s = 95
        if self.qid.get(a) and self.qid.get(a) == self.qid.get(b):
            s = max(s, 95)
        if self.topics[a] & self.topics[b]:
            s = max(s, 70)
        wa, wb = self.words[a], self.words[b]
        if wa and wb:
            s = max(s, int(100 * len(wa & wb) / len(wa | wb)))
        s = max(s, int(fuzz.ratio(max(self.forms[a] or {""}, key=len), max(self.forms[b] or {""}, key=len))) - 10)
        return s


def seed(kws: dict[str, dict], scope_ids: set[str]) -> list[dict]:
    """Buckets from reviewed synonyms: each topic's synonym keywords, plus accepted aliases."""
    by_syn = {k["master_synonym_id"]: kid for kid, k in kws.items()}
    by_name = defaultdict(list)
    for kid, k in kws.items():
        by_name[k["master_synonym"].lower()].append(kid)
    groups = defaultdict(set)
    for m in skills.read_csv(REF / "topic_mapping.csv"):
        if m["kind"] == "skill" and m.get("relation") == "synonym":
            for kid in ([by_syn[m["cinode_id"]]] if m["cinode_id"] in by_syn else []) + by_name.get(m["cinode_name"].lower(), []):
                groups[f"T{m['topic_id']}"].add(kid)
    for a in skills.read_csv(skills.ALIASES):
        if a["relation"] == "synonym":
            groups[f"A{a['canonical_id']}"].update({a["keyword_id"], a["canonical_id"]})
    rows, seen = [], set()
    for n, (_, members) in enumerate(sorted(groups.items()), 1):
        members = sorted((m for m in members if m in scope_ids and m not in seen), key=lambda m: order_key(kws[m]))
        if not members:
            continue
        head = kws[members[0]]["master_synonym"]
        for m in members:
            seen.add(m)
            rows.append({"keyword_id": m, "name": kws[m]["master_synonym"], "verified": kws[m]["is_verified"],
                         "users": kws[m]["users"], "bucket_id": len({r["bucket_id"] for r in rows}) + (m == members[0]),
                         "bucket_head": head, "decision": "seed", "matched_keyword": "" if m == members[0] else members[0],
                         "reason": "reviewed synonym (topic_mapping / skill_aliases)", "batch": 0, "model": "human"})
    # renumber bucket ids 1..n in order of appearance
    ids, out = {}, []
    for r in rows:
        key = r["bucket_head"]
        ids.setdefault(key, len(ids) + 1)
        r["bucket_id"] = ids[key]
        out.append(r)
    return out


def cmd_next(args) -> None:
    kws = skills.load_keywords()
    scope = scope_keywords(kws, args.scope)
    state = read_state()
    if not state:
        state = seed(kws, {k["keyword_id"] for k in scope})
        write_state(state)
        print(f"Seeded {len({r['bucket_id'] for r in state})} buckets from {len(state)} reviewed keywords", file=sys.stderr)
    done = {r["keyword_id"] for r in state}
    todo = [k for k in scope if k["keyword_id"] not in done][:args.size]
    batch_no = max([int(r["batch"]) for r in state] + [0]) + 1
    if not todo:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"batch": batch_no, "items": []}), encoding="utf-8")
        print(f"Nothing left in scope '{args.scope}' ({len(scope)} keywords, all assigned)")
        return
    sig = Signals(kws)
    buckets = defaultdict(list)
    for r in state:
        buckets[r["bucket_id"]].append(r["keyword_id"])
    items = []
    for i, k in enumerate(todo):
        kid = k["keyword_id"]
        scored = []
        for bid, members in buckets.items():
            best = max(sig.score(kid, m) for m in members)
            if best >= 50:
                scored.append((best, bid, members))
        scored.sort(key=lambda x: (-x[0], int(x[1])))
        shortlist = [{"bucket_id": int(bid), "head": kws[members[0]]["master_synonym"],
                      "members": [kws[m]["master_synonym"] for m in members[1:4]], "signal": s}
                     for s, bid, members in scored[:SHORTLIST]]
        earlier = sorted(((sig.score(kid, e["keyword_id"]), e) for e in todo[:i]), key=lambda x: -x[0])
        items.append({"keyword_id": kid, "name": k["master_synonym"], "verified": k["is_verified"], "users": k["users"],
                      "cinode_synonyms": k["syn_list"][:6], "shortlist": shortlist,
                      "earlier_in_batch": [{"keyword_id": e["keyword_id"], "name": e["master_synonym"]}
                                           for s, e in earlier[:8] if s >= 50]})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"batch": batch_no, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    left = len([k for k in scope if k["keyword_id"] not in done]) - len(items)
    print(f"Batch {batch_no}: {len(items)} keywords written to {args.out}; {left} more after this in scope '{args.scope}'")


def cmd_apply(args) -> None:
    kws = skills.load_keywords()
    data = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
    batch = json.loads(Path(args.batch).read_text(encoding="utf-8")) if args.batch else None
    state = read_state()
    where = {r["keyword_id"]: r for r in state}
    next_id = max([int(r["bucket_id"]) for r in state] + [0]) + 1
    heads = {str(r["bucket_id"]): r["bucket_head"] for r in state}
    expected = [it["keyword_id"] for it in batch["items"]] if batch else None
    decisions = data["decisions"]
    if expected is not None and [str(d["keyword_id"]) for d in decisions] != expected:
        sys.exit("Decisions must cover the batch keywords in the same order; nothing applied.")
    batch_no = batch["batch"] if batch else max([int(r["batch"]) for r in state] + [0]) + 1
    added = []
    for d in decisions:
        kid = str(d["keyword_id"])
        if kid in where or kid not in kws:
            sys.exit(f"Keyword {kid} is already assigned or unknown; nothing applied.")
        row = {"keyword_id": kid, "name": kws[kid]["master_synonym"], "verified": kws[kid]["is_verified"],
               "users": kws[kid]["users"], "decision": d["decision"], "reason": d.get("reason", "")[:300],
               "batch": batch_no, "matched_keyword": "", "model": args.model}
        if d["decision"] == "synonym":
            m = str(d.get("matched_keyword_id") or "")
            b = str(d.get("matched_bucket_id") or "")
            if m in where:  # a keyword examined earlier (usually earlier in this batch)
                row.update(bucket_id=where[m]["bucket_id"], bucket_head=where[m]["bucket_head"],
                           matched_keyword=kws[m]["master_synonym"])
            elif b in heads:  # a bucket from the shortlist
                row.update(bucket_id=b, bucket_head=heads[b], matched_keyword=heads[b])
            else:
                sys.exit(f"Keyword {kid}: no examined keyword {m!r} or bucket {b!r} to match; nothing applied.")
        elif d["decision"] == "new":
            row.update(bucket_id=next_id, bucket_head=kws[kid]["master_synonym"])
            next_id += 1
        else:
            sys.exit(f"Keyword {kid}: decision must be 'synonym' or 'new'; nothing applied.")
        where[kid] = row
        added.append(row)
    write_state(state + added)
    syn = sum(r["decision"] == "synonym" for r in added)
    print(f"Applied batch {batch_no}: {len(added)} keywords, {syn} joined a bucket, {len(added) - syn} new buckets")


def resolve(decisions: list[dict], state: list[dict]) -> dict[str, str]:
    """Bucket each decided keyword lands in: an existing bucket id, or "new:<head id>"."""
    where = {r["keyword_id"]: str(r["bucket_id"]) for r in state}
    for d in decisions:
        kid = str(d["keyword_id"])
        if d["decision"] == "synonym" and str(d.get("matched_keyword_id")) in where:
            where[kid] = where[str(d["matched_keyword_id"])]
        elif d["decision"] == "synonym" and d.get("matched_bucket_id"):
            where[kid] = str(d["matched_bucket_id"])
        else:
            where[kid] = f"new:{kid}"
    return {str(d["keyword_id"]): where[str(d["keyword_id"])] for d in decisions}


def cmd_compare(args) -> None:
    kws = skills.load_keywords()
    state = read_state()
    if args.before:  # judge both files against the buckets as they were before that batch
        state = [r for r in state if int(r["batch"]) < args.before]
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))["decisions"]
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))["decisions"]
    ra, rb = resolve(a, state), resolve(b, state)
    heads = {str(r["bucket_id"]): r["bucket_head"] for r in state}
    name = lambda bucket: heads.get(bucket) or ("new bucket" if bucket.startswith("new:") and bucket[4:] in ra
                                                 and False else kws.get(bucket[4:], {}).get("master_synonym", bucket))
    diff = [k for k in ra if ra[k] != rb.get(k)]
    print(f"{len(ra)} decisions, {len(ra) - len(diff)} agree, {len(diff)} differ")
    for k in diff:
        fa = "new bucket" if ra[k] == f"new:{k}" else f"with {name(ra[k])}"
        fb = "new bucket" if rb[k] == f"new:{k}" else f"with {name(rb[k])}"
        print(f"  {kws[k]['master_synonym']}: {Path(args.a).stem} {fa} | {Path(args.b).stem} {fb}")


def renumber_heads(state: list[dict], kws: dict[str, dict]) -> None:
    """Each bucket's head is its most established member (verified, most used, name)."""
    members = defaultdict(list)
    for r in state:
        members[str(r["bucket_id"])].append(r)
    for rows in members.values():
        head = min(rows, key=lambda r: order_key(kws[r["keyword_id"]]))["name"]
        for r in rows:
            r["bucket_head"] = head


def revise(state: list[dict], kws: dict[str, dict], ops: list[dict], batch_no: int, model: str) -> list[str]:
    """Apply merge/split operations in place; returns a log line per operation.

    merge: {"op": "merge", "a": kid, "b": kid} moves every keyword of b's bucket into a's
    (the bucket with the lower id survives). split: {"op": "split", "keyword_id": kid} gives
    the keyword a bucket of its own.
    """
    where = {r["keyword_id"]: r for r in state}
    next_id = max(int(r["bucket_id"]) for r in state) + 1
    log = []
    for op in ops:
        reason = op.get("reason", "")[:300]
        if op["op"] == "merge":
            a, b = where[str(op["a"])], where[str(op["b"])]
            ba, bb = str(a["bucket_id"]), str(b["bucket_id"])
            if ba == bb:
                continue
            keep, drop = sorted((ba, bb), key=int)
            other = b if keep == ba else a
            for r in state:
                if str(r["bucket_id"]) == drop:
                    r.update(bucket_id=keep, decision="revised", batch=batch_no, model=model, reason=reason,
                             matched_keyword=(a if other is b else b)["name"])
            log.append(f"merged {b['name']} -> {a['name']}")
        elif op["op"] == "split":
            r = where[str(op["keyword_id"])]
            r.update(bucket_id=next_id, decision="revised", batch=batch_no, model=model, reason=reason,
                     matched_keyword="")
            next_id += 1
            log.append(f"split {r['name']}")
        else:
            raise ValueError(f"unknown op {op['op']!r}")
    renumber_heads(state, kws)
    return log


def cmd_revise(args) -> None:
    kws = skills.load_keywords()
    state = read_state()
    ops = json.loads(Path(args.ops).read_text(encoding="utf-8"))["ops"]
    batch_no = max(int(r["batch"]) for r in state) + 1
    for line in revise(state, kws, ops, batch_no, args.model):
        print(line)
    write_state(state)


ROLE_TO_DISCIPLINE = {
    "manager": "management", "owner": "ownership", "architect": "architecture", "developer": "development",
    "engineer": "engineering", "tester": "testing", "designer": "design", "analyst": "analysis",
    "lead": "leadership", "leader": "leadership", "mentor": "mentoring", "coach": "coaching",
    "consultant": "consulting", "administrator": "administration", "admin": "administration",
    "utvecklare": "development", "utvikler": "development", "arkitekt": "architecture",
    "projektledare": "project management", "testare": "testing", "ledare": "leadership",
}


def role_words(name: str) -> frozenset[str]:
    out = set()
    for w in words(name):
        out.update(ROLE_TO_DISCIPLINE.get(w, w).split())
    return frozenset(skills._singular(w) for w in out)


def pair_score(sig: Signals, a: str, b: str) -> int:
    """Like Signals.score but stricter on abbreviations (an all-caps short name that spells
    the other's initials exactly), since this pass compares every bucket with every other."""
    if sig.forms[a] & sig.forms[b]:
        return 100
    if sig.qid.get(a) and sig.qid.get(a) == sig.qid.get(b):
        return 95
    na, nb = sig.kws[a]["master_synonym"], sig.kws[b]["master_synonym"]
    for long, short in ((na, nb), (nb, na)):
        letters = re.sub(r"[^A-Za-z0-9]", "", short)
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", long))
        if 2 <= len(letters) <= 6 and letters.isupper() and len(initials) >= 2 and letters.lower() == initials.lower():
            return 90
    wa, wb = sig.words[a], sig.words[b]
    s = int(100 * len(wa & wb) / len(wa | wb)) if wa and wb else 0
    fa, fb = max(sig.forms[a] or {""}, key=len), max(sig.forms[b] or {""}, key=len)
    return max(s, int(fuzz.ratio(fa, fb)) - 5)


def candidate_pairs(state: list[dict], kws: dict[str, dict], min_score: int = 80) -> list[tuple]:
    """Bucket pairs worth a second look: high name similarity, shared Wikidata item, or the
    same words once roles are mapped to their discipline."""
    sig = Signals(kws)
    members = defaultdict(list)
    for r in state:
        members[str(r["bucket_id"])].append(r["keyword_id"])
    by_role = defaultdict(set)
    for b, ms in members.items():
        for m in ms:
            rw = role_words(kws[m]["master_synonym"])
            if rw:
                by_role[rw].add(b)
    pairs = {}
    for bs in by_role.values():
        bs = sorted(bs, key=int)
        for i, x in enumerate(bs):
            for y in bs[i + 1:]:
                pairs[(x, y)] = 99
    ids = sorted(members, key=int)
    for i, x in enumerate(ids):
        for y in ids[i + 1:]:
            if (x, y) in pairs:
                continue
            s = max(pair_score(sig, p, q) for p in members[x] for q in members[y])
            if s >= min_score:
                pairs[(x, y)] = s
    return sorted(((s, x, y) for (x, y), s in pairs.items()), key=lambda t: (-t[0], int(t[1]), int(t[2])))


def cmd_pairs(args) -> None:
    kws = skills.load_keywords()
    state = read_state()
    members = defaultdict(list)
    for r in state:
        members[str(r["bucket_id"])].append(r["keyword_id"])
    out = []
    for s, x, y in candidate_pairs(state, kws, args.min_score):
        desc = lambda b: {"bucket_id": int(b), "members": [
            {"keyword_id": m, "name": kws[m]["master_synonym"], "cinode_synonyms": kws[m]["syn_list"][:5]}
            for m in members[b][:5]]}
        out.append({"signal": s, "a": desc(x), "b": desc(y)})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"rules": JUDGE_RULES, "pairs": out}, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"{len(out)} candidate pairs written to {args.out}")


def cmd_rules(args) -> None:
    print(JUDGE_RULES)


def cmd_stats(args) -> None:
    state = read_state()
    sizes = defaultdict(list)
    for r in state:
        sizes[r["bucket_id"]].append(r["name"])
    multi = {b: v for b, v in sizes.items() if len(v) > 1}
    print(f"{len(state)} keywords in {len(sizes)} buckets; {len(multi)} buckets with 2+ keywords; "
          f"decisions: " + ", ".join(f"{d} {sum(r['decision'] == d for r in state)}" for d in ("seed", "synonym", "new", "revised")))
    if args.show:
        for b, v in sorted(multi.items(), key=lambda x: -len(x[1]))[:args.show]:
            print(f"  {b}: " + " / ".join(v))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("next", help="write the next batch with shortlists")
    n.add_argument("--scope", choices=["pilot", "used2", "all"], default="pilot")
    n.add_argument("--size", type=int, default=50)
    n.add_argument("--out", default=str(ROOT / "data" / "work" / "batch.json"))
    a = sub.add_parser("apply", help="apply a subagent's decisions for a batch")
    a.add_argument("decisions")
    a.add_argument("--batch", default=str(ROOT / "data" / "work" / "batch.json"),
                   help="the batch file the decisions answer (checked for order)")
    a.add_argument("--model", default="", help="model that made the decisions (recorded per row)")
    c = sub.add_parser("compare", help="compare two decision files for the same batch")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--before", type=int, default=0, help="batch number the decisions answer")
    s = sub.add_parser("stats", help="bucket counts")
    s.add_argument("--show", type=int, default=0, help="also list the N largest buckets")
    r = sub.add_parser("revise", help="apply merge/split operations to the buckets")
    r.add_argument("ops", help='JSON {"ops": [{"op": "merge", "a": kid, "b": kid} | {"op": "split", "keyword_id": kid}]}')
    r.add_argument("--model", default="", help="who decided (recorded per changed row)")
    p = sub.add_parser("pairs", help="candidate bucket pairs for a missed-merge pass")
    p.add_argument("--min-score", type=int, default=80)
    p.add_argument("--out", default=str(ROOT / "data" / "work" / "pairs.json"))
    sub.add_parser("rules", help="print the judging rules given to the subagents")
    args = ap.parse_args()
    {"next": cmd_next, "apply": cmd_apply, "stats": cmd_stats, "compare": cmd_compare, "revise": cmd_revise,
     "pairs": cmd_pairs, "rules": cmd_rules}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
