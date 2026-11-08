"""AI hot-topic layer on top of the skill taxonomy.

The general taxonomy (keywords, alias buckets, marketable skills) lives in data/taxonomy/
and src/skills.py + src/buckets.py and knows nothing about AI. This module holds what is
AI-specific: the sales team's hot topics, the mapping of Cinode keywords to them, the
curated canonical AI skills with definitions, and the catalogue page data. It refers to
taxonomy keywords by id and never duplicates them.

    uv run src/ai_topics.py seed      # seed data/ai/canonical_skills.csv from hot_topics.csv (if missing)
    uv run src/ai_topics.py report    # data/ai/reports/ai_verified_keywords.csv
    uv run src/ai_topics.py view      # data/ai/canonical_skills_view.json (catalogue page data)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skills  # noqa: E402

AI = skills.AI
HOT_TOPICS = AI / "hot_topics.csv"
TOPIC_MAPPING = AI / "topic_mapping.csv"
CANONICAL = AI / "canonical_skills.csv"
REPORTS = AI / "reports"


def seed_canonical(kws: dict[str, dict]) -> None:

    """Seed canonical_skills.csv from the 30 hot topics (only when the file does not exist)."""
    if CANONICAL.exists():
        return
    by_syn = {k["master_synonym_id"]: k for k in kws.values()}
    mapping = skills.read_csv(TOPIC_MAPPING)
    rows = []
    for t in skills.read_csv(HOT_TOPICS):
        ks = [by_syn[m["cinode_id"]] for m in mapping
              if m["topic_id"] == t["topic_id"] and m["kind"] == "skill" and m["match"] == "exact"
              and m["cinode_id"] in by_syn]
        best = max(ks, key=lambda k: (k["is_verified"], k["users"]), default=None)
        rows.append({"id": f"T{int(t['topic_id']):02d}", "name": t["topic"], "definition": "",
                     "domain": t["category"], "ai_related": t["topic_id"] not in {"15", "22"},
                     "preferred_keyword_id": best["keyword_id"] if best else "", "wikidata_qid": ""})
    skills.write_csv(CANONICAL, rows, ["id", "name", "definition", "domain", "ai_related",
                                "preferred_keyword_id", "wikidata_qid"])
    print(f"Seeded {CANONICAL.relative_to(skills.ROOT)} with {len(rows)} skills from hot_topics.csv")


def topic_index() -> tuple[dict[str, str], dict[str, str]]:
    """Hot topic by mapped synonym id and by lowercase name (skill rows only), as "T05" for a
    synonym of the topic's skill or "T05 product" / "T05 narrower" / "T05 related" otherwise."""
    by_id, by_name = {}, {}
    for m in skills.read_csv(TOPIC_MAPPING):
        if m["kind"] == "skill":
            rel = m.get("relation") or "related"
            t = f"T{int(m['topic_id']):02d}" + ("" if rel == "synonym" else f" {rel}")
            by_id.setdefault(m["cinode_id"], t)
            by_name.setdefault(m["cinode_name"].lower(), t)
    return by_id, by_name


def cmd_report(args) -> None:
    """Verified keywords that are AI-related (mapped to an AI topic, or under AI/ML in Wikidata)."""
    kws = skills.load_keywords()
    links = {r["keyword_id"]: r for r in skills.read_csv(skills.LINKS) if r["qid"]}
    aliases = skills.read_csv(skills.ALIASES)
    canon = skills.read_csv(CANONICAL)
    mapping = skills.read_csv(TOPIC_MAPPING)
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

    out_a.sort(key=lambda r: (-r["techseed_users"], int(r["keyword_id"])))
    path = REPORTS / "ai_verified_keywords.csv"
    skills.write_csv(path, out_a, list(out_a[0].keys()) if out_a else ["keyword_id"])
    print(f"ai_verified_keywords {len(out_a):>5} keywords. Wrote {path.relative_to(skills.ROOT)}")


def cmd_view(args) -> None:
    """Build the catalogue page data: one record per canonical skill with its proposed keyword,
    definition and every other keyword that belongs to it, split by relation."""
    kws = skills.load_keywords()
    by_syn = {k["master_synonym_id"]: kid for kid, k in kws.items()}
    by_name = defaultdict(list)
    for kid, k in kws.items():
        by_name[k["master_synonym"].lower()].append(kid)
    hot = {f"T{int(t['topic_id']):02d}": t["topic"] for t in skills.read_csv(HOT_TOPICS)}
    prop = {p["topic_id"]: p for p in skills.read_csv(AI / "ai_skillset_proposal.csv")}
    rel_table = json.loads((AI / "skill_relations.json").read_text(encoding="utf-8"))

    members: dict[str, dict[str, tuple]] = defaultdict(dict)  # topic -> keyword -> (source, match, relation)
    for m in skills.read_csv(TOPIC_MAPPING):
        if m["kind"] != "skill":
            continue
        t = f"T{int(m['topic_id']):02d}"
        for kid in ([by_syn[m["cinode_id"]]] if m["cinode_id"] in by_syn else []) + by_name.get(m["cinode_name"].lower(), []):
            if kid not in members[t] or m["match"] == "exact":
                members[t][kid] = ("mapping", m["match"], m.get("relation") or "related")
    for a in skills.read_csv(skills.ALIASES):
        for t, ks in members.items():
            if a["relation"] in skills.RELATIONS and a["canonical_id"] in ks:
                ks.setdefault(a["keyword_id"], ("alias", "reviewed", a["relation"]))
    for q in skills.read_csv(skills.QUEUE):  # strong unreviewed suggestions to a keyword of the topic
        for n in (1, 2, 3):
            target, method = q.get(f"s{n}_id"), q.get(f"s{n}_method")
            if not target or method not in {"alias", "exact", "cinode", "wikidata"}:
                continue
            for t, ks in members.items():
                if target in ks and ks[target][0] == "mapping":
                    rel = rel_table.get(t, {}).get(q["keyword_name"], "related")
                    ks.setdefault(q["keyword_id"], ("suggested", method, rel))

    out = []
    for c in skills.read_csv(CANONICAL):
        tid, p = c["id"], prop.get(c["id"], {})
        chosen = p.get("keyword_id", "")
        syn = [{"name": kws[k]["master_synonym"], "verified": kws[k]["is_verified"], "users": kws[k]["users"],
                "source": src, "match": match, "relation": rel}
               for k, (src, match, rel) in members[tid].items() if k != chosen and k in kws]
        syn.sort(key=lambda x: (skills.RELATIONS.index(x["relation"]), -x["users"], x["name"].lower()))
        out.append({"topic_id": tid, "topic": hot.get(tid, c["name"]), "canonical": p.get("proposed_keyword", c["name"]),
                    "canonical_verified": p.get("status") == "verified",
                    "canonical_users": int(p.get("users_on_profiles") or 0),
                    "needs_new_keyword": p.get("status") == "needs new keyword", "hot": tid in hot,
                    "ai_related": c["ai_related"] == "True", "domain": c["domain"], "definition": c["definition"],
                    "synonyms": syn})
    path = AI / "canonical_skills_view.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    counts = defaultdict(int)
    for r in out:
        for x in r["synonyms"]:
            counts[x["relation"]] += 1
    print(f"Wrote {path.relative_to(skills.ROOT)}: {len(out)} skills; " + ", ".join(f"{counts[r]} {r}" for r in skills.RELATIONS))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="seed canonical_skills.csv from the hot topics (only if missing)")
    sub.add_parser("report", help="write data/ai/reports/ai_verified_keywords.csv")
    sub.add_parser("view", help="build data/ai/canonical_skills_view.json for the catalogue page")
    args = ap.parse_args()
    if args.cmd == "seed":
        seed_canonical(skills.load_keywords())
    else:
        {"report": cmd_report, "view": cmd_view}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
