"""Round-trip human curation of skill sets, aliases and suggested keywords via Excel.

    uv run src/curate.py export                 # data/curation/curation_<date>.xlsx
    #   people edit the Keywords and Skill sets sheets in Excel (or Google Sheets / Numbers)
    uv run src/curate.py check  FILE.xlsx       # validate + diff report, changes nothing
    uv run src/curate.py apply  FILE.xlsx       # same, then writes skill_buckets.csv + marketable_skills.csv
    uv run src/curate.py ai-sync [--apply]      # AI topics with a skill_set in data/ai/ai_skillset_proposal.csv

Model (one row per keyword in the Keywords sheet):
  skill_set  the level-2 marketable skill the keyword belongs to (buyer-facing headline)
  alias_of   blank = the keyword stands on its own (a level-1 bucket head); otherwise the name
             of the keyword it is an alias of. Aliases stay in their head's skill set.
  suggested  x = the keyword we suggest consultants pick for this skill set (one or more per set)

The Skill sets sheet lists every skill set; type a new name in `rename_to` to rename one, or
the same new name on two rows to merge them. A new skill set is created by typing a new name in
a keyword's `skill_set` cell. Rows copied in from the More keywords sheet join the taxonomy.

Workbooks and reports are written to data/curation/ (gitignored). Nothing is sent to Cinode.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict
from difflib import get_close_matches
from pathlib import Path

from openpyxl import Workbook, load_workbook
from rapidfuzz import fuzz
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buckets  # noqa: E402
import skills  # noqa: E402

OUT = skills.ROOT / "data" / "curation"
KW_SHEET, SET_SHEET, MORE_SHEET, README_SHEET = "Keywords", "Skill sets", "More keywords", "Read me"
KW_COLS = ["keyword_id", "keyword", "verified", "people", "skill_set", "alias_of", "suggested", "comment",
           "cinode_type", "was_skill_set", "was_alias_of", "was_suggested", "changed"]
EDITABLE = {"skill_set", "alias_of", "suggested", "comment"}
SET_COLS = ["skill_id", "skill_set", "rename_to", "keywords", "keyword_uses", "people_at_export",
            "suggested_at_export", "comment"]
YES = {"x", "yes", "y", "1", "true", "ja", "sant"}
MAX_ROWS = 20000

GREY = PatternFill("solid", fgColor="EEEEEE")
EDIT = PatternFill("solid", fgColor="FFF8DC")
HEAD = PatternFill("solid", fgColor="DDEBF7")

README = """\
Skill curation workbook

Skills are a sales tool: consultant buyers search them. Judge every move with the buyer test:
would a buyer searching for one keyword consider a consultant listing the other to have the same
marketable skill?

'people' in the Keywords sheet is the number of TechSeed employees listing that keyword.

Edit only the yellow columns in the Keywords sheet:
  skill_set   which skill set (buyer-facing headline) the keyword belongs to. Pick from the list,
              or type a new name to create a new skill set.
  alias_of    leave blank if the keyword stands on its own. Otherwise type (or pick) the keyword it
              is an alias of, e.g. "Python3" alias_of "Python". The target must be in the same
              skill set and must not itself be an alias. Moving a keyword that has aliases moves
              nothing else: move its aliases too, or point them at another keyword.
  suggested   x marks the keyword consultants should pick for this skill set. Only a keyword that
              stands on its own can be suggested. Usually one per skill set.
  comment     free text; kept as the reason for the change.

Skill sets sheet: type a new name in rename_to to rename a skill set. Give two skill sets the same
rename_to to merge them. keywords and keyword_uses update as you edit. keyword_uses adds up
'people' per keyword, so a person listing two keywords of a skill set counts twice; it measures
how often the skill set's keywords are used, not how many people have it. people_at_export is the
number of distinct employees (filled only when profile data is loaded locally).

More keywords sheet: keywords used by only one person (not yet in the taxonomy). To bring one in,
copy its row into the bottom of the Keywords sheet and fill in skill_set.

Do not delete rows, and do not edit keyword_id or the grey columns. Sorting and filtering are fine.
The 'changed' column flags rows that differ from the export.

When done, save as .xlsx and run:  uv run src/curate.py check <file>   then   apply <file>
"""


# ---------- shared ----------

def norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def kid(v) -> str:
    s = norm(v)
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def default_suggested(rows: list[dict], kws: dict[str, dict]) -> set[str]:
    """One keyword per skill set: the bucket head named like the skill set, else the head of the
    most-used bucket (verified heads first)."""
    by_skill = defaultdict(list)
    for r in rows:
        by_skill[r["skill_id"]].append(r)
    out = set()
    for sid, rs in by_skill.items():
        heads = {}
        for r in rs:
            heads.setdefault(r["bucket_head"], []).append(r)
        name = rs[0]["skill_name"].casefold()

        def rank(h):
            head_row = next(x for x in heads[h] if x["name"] == h)
            return (h.casefold() != name, fuzz.ratio(h.casefold(), name) < 90, head_row["verified"] != "True",
                    -buckets.bucket_users(heads[h]), h.lower())
        pick = min(heads, key=rank)
        out.add(next(r["keyword_id"] for r in rs if r["name"] == pick))
    return out


def current_view(state: list[dict]) -> dict[str, dict]:
    """keyword_id -> {skill_set, alias_of, suggested} as the workbook shows it."""
    if state and "suggested" not in state[0]:  # never curated: pick a default per skill set
        sug = default_suggested(state, skills.load_keywords())
    else:
        sug = {r["keyword_id"] for r in state if r.get("suggested") in ("1", "True", "x")}
    return {r["keyword_id"]: {"skill_set": norm(r["skill_name"]),
                              "alias_of": "" if r["name"] == r["bucket_head"] else norm(r["bucket_head"]),
                              "suggested": "x" if r["keyword_id"] in sug else ""} for r in state}


# ---------- export ----------

def people_per_skill(state: list[dict]) -> dict[str, int]:
    """skill_id -> distinct employees with any of its keywords (aggregate only); {} without profile data."""
    db = skills.ROOT / "db" / "cinode.duckdb"
    if not db.exists():
        return {}
    import duckdb
    try:
        con = duckdb.connect(str(db), read_only=True)
        pairs = con.sql("SELECT DISTINCT CAST(keyword_id AS VARCHAR), company_user_id FROM employee_skills").fetchall()
    except duckdb.Error:
        return {}
    skill_of = {r["keyword_id"]: r["skill_id"] for r in state}
    who = defaultdict(set)
    for k, u in pairs:
        if k in skill_of:
            who[skill_of[k]].add(u)
    return {s: len(v) for s, v in who.items()}


def cmd_export(args) -> None:
    state = buckets.read_state()
    kws = skills.load_keywords()
    view = current_view(state)
    by_skill = defaultdict(list)
    for r in state:
        by_skill[r["skill_id"]].append(r)

    wb = Workbook()
    ws_r = wb.active
    ws_r.title = README_SHEET
    for i, line in enumerate(README.splitlines(), 1):
        ws_r.cell(i, 1, line).font = Font(name="Menlo", size=11, bold=(i == 1))
    ws_r.column_dimensions["A"].width = 110

    people = people_per_skill(state)
    ws = wb.create_sheet(KW_SHEET)
    ws.append(KW_COLS)
    order = sorted(by_skill, key=lambda s: (-buckets.bucket_users(by_skill[s]), by_skill[s][0]["skill_name"]))
    n = 1
    for sid in order:
        groups = defaultdict(list)
        for r in by_skill[sid]:
            groups[r["bucket_head"]].append(r)
        for head in sorted(groups, key=lambda h: (-buckets.bucket_users(groups[h]), h.lower())):
            rows = sorted(groups[head], key=lambda r: (r["name"] != head, -int(r["users"] or 0), r["name"].lower()))
            for r in rows:
                n += 1
                v = view[r["keyword_id"]]
                k = kws.get(r["keyword_id"], {})
                ws.append([int(r["keyword_id"]), r["name"], r["verified"] == "True", k.get("users", int(r["users"] or 0)),
                           v["skill_set"], v["alias_of"], v["suggested"], "", k.get("type_name", ""),
                           v["skill_set"], v["alias_of"], v["suggested"],
                           f'=IF(OR(E{n}<>J{n},F{n}<>K{n},G{n}<>L{n}),"changed","")'])
                if not v["alias_of"]:
                    ws.cell(n, 2).fill = HEAD
    _style_keywords(ws, n)

    ws_s = wb.create_sheet(SET_SHEET)
    ws_s.append(SET_COLS)
    for i, sid in enumerate(order, 2):
        rs = by_skill[sid]
        sug = "; ".join(r["name"] for r in rs if view[r["keyword_id"]]["suggested"])
        ws_s.append([int(sid), rs[0]["skill_name"], "", f"=COUNTIF('{KW_SHEET}'!$E:$E,B{i})",
                     f"=SUMIF('{KW_SHEET}'!$E:$E,B{i},'{KW_SHEET}'!$D:$D)", people.get(sid), sug, ""])
    _style_table(ws_s, {"skill_id": 9, "skill_set": 38, "rename_to": 30, "keywords": 10, "keyword_uses": 13,
                        "people_at_export": 16, "suggested_at_export": 40, "comment": 40},
                 editable={"rename_to", "comment"})
    ws_s.cell(1, SET_COLS.index("keyword_uses") + 1).comment = Comment(
        "Sum of 'people' over the skill set's keywords. One person listing two of its keywords counts twice, "
        "so this is keyword uses, not people. Updates as you edit.", "curate")
    ws_s.cell(1, SET_COLS.index("people_at_export") + 1).comment = Comment(
        "Distinct employees with at least one keyword of the skill set, at export time (from the local "
        "profile data). Blank when profiles are not loaded. Does not update as you edit.", "curate")

    ws_m = wb.create_sheet(MORE_SHEET)
    ws_m.append(["keyword_id", "keyword", "verified", "people", "cinode_type", "synonyms"])
    in_state = {r["keyword_id"] for r in state}
    more = sorted((k for k in kws.values() if k["keyword_id"] not in in_state),
                  key=lambda k: (-k["users"], not k["is_verified"], k["master_synonym"].lower()))
    for k in more:
        ws_m.append([int(k["keyword_id"]), k["master_synonym"], k["is_verified"], k["users"], k["type_name"],
                     "; ".join(k["syn_list"][:8])])
    _style_table(ws_m, {"keyword_id": 11, "keyword": 40, "verified": 9, "people": 7, "cinode_type": 18,
                        "synonyms": 80}, editable=set())

    OUT.mkdir(parents=True, exist_ok=True)
    path = Path(args.out) if args.out else OUT / f"curation_{dt.date.today():%Y-%m-%d}.xlsx"
    wb.save(path)
    if not people:
        print("No profile data in db/cinode.duckdb: people_at_export left blank (run load_duckdb.py with profiles)")
    print(f"Wrote {path.relative_to(skills.ROOT) if path.is_relative_to(skills.ROOT) else path}: "
          f"{n - 1} keywords, {len(order)} skill sets, {len(more)} more keywords")


def _style_table(ws, widths: dict[str, int], editable: set[str]) -> None:
    for j, (col, w) in enumerate(widths.items(), 1):
        letter = ws.cell(1, j).column_letter
        ws.column_dimensions[letter].width = w
        c = ws.cell(1, j)
        c.font = Font(bold=True)
        c.fill = EDIT if col in editable else GREY
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions


def _style_keywords(ws, last: int) -> None:
    widths = {"keyword_id": 11, "keyword": 36, "verified": 9, "people": 7, "skill_set": 32, "alias_of": 30,
              "suggested": 10, "comment": 30, "cinode_type": 16, "was_skill_set": 26, "was_alias_of": 22,
              "was_suggested": 9, "changed": 9}
    _style_table(ws, widths, EDITABLE)
    for j, col in enumerate(KW_COLS, 1):
        if col in EDITABLE:
            for i in range(2, last + 1):
                ws.cell(i, j).fill = EDIT
    for col in ("was_skill_set", "was_alias_of", "was_suggested", "changed"):
        ws.cell(1, KW_COLS.index(col) + 1).font = Font(bold=True, color="888888")
    ws.cell(1, KW_COLS.index("alias_of") + 1).comment = Comment(
        "Blank = stands on its own. Otherwise the keyword this one is an alias of (same skill set).", "curate")
    ws.cell(1, KW_COLS.index("suggested") + 1).comment = Comment(
        "x = the keyword consultants should pick for this skill set.", "curate")
    rng = lambda col: f"{ws.cell(1, KW_COLS.index(col) + 1).column_letter}2:" \
                      f"{ws.cell(1, KW_COLS.index(col) + 1).column_letter}{MAX_ROWS}"
    dv_set = DataValidation(type="list", formula1=f"='{SET_SHEET}'!$B$2:$B${MAX_ROWS}", allow_blank=True,
                            errorStyle="warning", showErrorMessage=True, errorTitle="New skill set?",
                            error="Not an existing skill set. Keep it to create a new skill set.")
    dv_alias = DataValidation(type="list", formula1=f"=$B$2:$B${MAX_ROWS}", allow_blank=True,
                              errorStyle="warning", showErrorMessage=True, errorTitle="Unknown keyword",
                              error="alias_of should be a keyword name from column B.")
    dv_sug = DataValidation(type="list", formula1='"x"', allow_blank=True)
    for dv, col in ((dv_set, "skill_set"), (dv_alias, "alias_of"), (dv_sug, "suggested")):
        ws.add_data_validation(dv)
        dv.add(rng(col))


# ---------- import ----------

class Result:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.changes: dict[str, list[str]] = defaultdict(list)


def read_sheet(wb, name: str) -> list[dict]:
    if name not in wb.sheetnames:
        return []
    rows = wb[name].iter_rows(values_only=True)
    header = [norm(h) for h in next(rows, [])]
    out = []
    for i, row in enumerate(rows, 2):
        d = {h: row[j] if j < len(row) else None for j, h in enumerate(header) if h}
        if any(norm(v) for v in d.values()):
            d["_row"] = i
            out.append(d)
    return out


def curate(state: list[dict], kws: dict[str, dict], kw_rows: list[dict], set_rows: list[dict]) -> tuple[list[dict], Result]:
    """Validate the edited sheets against the current state; return (new_state, result)."""
    res = Result()
    before = current_view(state)
    old = {r["keyword_id"]: r for r in state}
    old_skill_ids = {norm(r["skill_name"]): r["skill_id"] for r in state}

    # skill set renames / merges
    rename = {}
    for s in set_rows:
        name, new = norm(s.get("skill_set")), norm(s.get("rename_to"))
        if name and new and new != name:
            rename[name] = new
    targets = defaultdict(list)
    for a, b in rename.items():
        targets[b].append(a)
    for b, srcs in targets.items():
        if b in old_skill_ids and b not in rename:
            srcs = srcs + [b]
        if len(srcs) > 1:
            res.changes["Skill sets merged"].append(f"{' + '.join(sorted(srcs))} -> {b}")
        else:
            res.changes["Skill sets renamed"].append(f"{srcs[0]} -> {b}")

    # keyword rows
    rows, seen = {}, {}
    for r in kw_rows:
        k = kid(r.get("keyword_id"))
        where = f"Keywords row {r['_row']}"
        if not k:
            res.errors.append(f"{where}: no keyword_id")
            continue
        if k in seen:
            res.errors.append(f"{where}: keyword_id {k} also on row {seen[k]}")
            continue
        if k not in old and k not in kws:
            res.errors.append(f"{where}: keyword_id {k} is not a known Cinode keyword")
            continue
        seen[k] = r["_row"]
        name = old[k]["name"] if k in old else kws[k]["master_synonym"]
        if norm(r.get("keyword")) and norm(r.get("keyword")) != norm(name):
            res.warnings.append(f"{where}: keyword name edited ('{norm(r.get('keyword'))}'); names come from "
                                f"Cinode, kept '{name}'")
        sset = norm(r.get("skill_set"))
        sset = rename.get(sset, sset)
        rows[k] = {"name": name, "skill_set": sset, "alias_of": norm(r.get("alias_of")),
                   "suggested": norm(r.get("suggested")).lower() in YES, "comment": norm(r.get("comment")),
                   "row": r["_row"]}
        if not sset:
            res.errors.append(f"{where} ({name}): skill_set is empty")
        was = (norm(r.get("was_skill_set")), norm(r.get("was_alias_of")), norm(r.get("was_suggested")))
        if k in before and any(was) and was != tuple(before[k][c] for c in ("skill_set", "alias_of", "suggested")):
            res.warnings.append(f"{where} ({name}): the taxonomy changed since this workbook was exported")
    for k in old:
        if k not in rows and k not in seen:
            res.errors.append(f"keyword {old[k]['name']} ({k}) is missing from the Keywords sheet (rows must not be deleted)")

    by_name = {norm(v["name"]).casefold(): k for k, v in rows.items()}

    # aliases: resolve to heads
    head_of = {}
    for k, v in rows.items():
        if not v["alias_of"]:
            continue
        t = by_name.get(v["alias_of"].casefold())
        if t is None and kid(v["alias_of"]) in rows:
            t = kid(v["alias_of"])
        if t is None:
            hint = get_close_matches(v["alias_of"], [x["name"] for x in rows.values()], n=1)
            res.errors.append(f"Keywords row {v['row']} ({v['name']}): alias_of '{v['alias_of']}' is not a keyword"
                              + (f" (did you mean '{hint[0]}'?)" if hint else ""))
        elif t == k:
            v["alias_of"] = ""
        else:
            head_of[k] = t
    for k in list(head_of):
        chain, t = [k], head_of[k]
        while t in head_of and t not in chain:
            chain.append(t)
            t = head_of[t]
        if t in chain:
            res.errors.append(f"alias loop: {' -> '.join(rows[x]['name'] for x in chain + [t])}")
            continue
        if len(chain) > 1:
            res.warnings.append(f"{rows[k]['name']}: alias_of '{rows[head_of[k]]['name']}' is itself an alias; "
                                f"using '{rows[t]['name']}'")
        head_of[k] = t
    for k, h in head_of.items():
        if rows[k]["skill_set"] and rows[h]["skill_set"] and rows[k]["skill_set"] != rows[h]["skill_set"]:
            res.errors.append(f"Keywords row {rows[k]['row']} ({rows[k]['name']}): alias of '{rows[h]['name']}' "
                              f"but in skill set '{rows[k]['skill_set']}' (it is in '{rows[h]['skill_set']}')")
        if rows[k]["suggested"]:
            res.errors.append(f"Keywords row {rows[k]['row']} ({rows[k]['name']}): suggested, but it is an alias "
                              f"of '{rows[h]['name']}'; suggest the head instead")

    # skill sets
    members = defaultdict(list)
    for k, v in rows.items():
        if v["skill_set"]:
            members[v["skill_set"]].append(k)
    existing = {rename.get(n, n) for n in old_skill_ids}
    listed = existing | {rename.get(norm(s.get("skill_set")), norm(s.get("skill_set"))) for s in set_rows}
    for s, ks in members.items():
        if s not in existing:
            msg = f"'{s}' ({', '.join(rows[k]['name'] for k in ks[:5])})"
            hint = None if s in listed else get_close_matches(s, sorted(existing), n=1, cutoff=0.85)
            if hint:
                res.warnings.append(f"new skill set {msg}: typo for '{hint[0]}'?")
            res.changes["Skill sets created"].append(msg)
        if not any(rows[k]["suggested"] for k in ks):
            res.warnings.append(f"skill set '{s}' has no suggested keyword")
        elif sum(rows[k]["suggested"] for k in ks) > 1:
            res.warnings.append(f"skill set '{s}' has {sum(rows[k]['suggested'] for k in ks)} suggested keywords: "
                                + ", ".join(rows[k]["name"] for k in ks if rows[k]["suggested"]))
    for n in sorted({rename.get(n, n) for n in old_skill_ids} - set(members)):
        res.changes["Skill sets emptied (removed)"].append(n)

    if res.errors:
        return state, res

    # diff per keyword
    for k, v in sorted(rows.items(), key=lambda x: (x[1]["skill_set"], x[1]["name"].lower())):
        head = rows[head_of[k]]["name"] if k in head_of else ""
        b = before.get(k)
        note = f"  ({v['comment']})" if v["comment"] else ""
        if b is None:
            res.changes["Keywords added"].append(f"{v['name']} -> {v['skill_set']}" + (f", alias of {head}" if head else "") + note)
            continue
        if rename.get(b["skill_set"], b["skill_set"]) != v["skill_set"]:
            res.changes["Moved to another skill set"].append(f"{v['name']}: {b['skill_set']} -> {v['skill_set']}{note}")
        if b["alias_of"] != head:
            if head and b["alias_of"]:
                res.changes["Alias changed"].append(f"{v['name']}: alias of {b['alias_of']} -> alias of {head}{note}")
            elif head:
                res.changes["Became an alias"].append(f"{v['name']} -> alias of {head}{note}")
            else:
                res.changes["Split off (stands on its own)"].append(f"{v['name']} (was alias of {b['alias_of']}){note}")
        if bool(b["suggested"]) != v["suggested"]:
            res.changes["Suggested" if v["suggested"] else "No longer suggested"].append(
                f"{v['name']} ({v['skill_set']}){note}")

    return build_state(state, kws, rows, head_of, old_skill_ids, rename, before), res


def build_state(state, kws, rows, head_of, old_skill_ids, rename, before) -> list[dict]:
    old = {r["keyword_id"]: r for r in state}
    batch_no = max(int(r["batch"] or 0) for r in state) + 1
    # skill ids: keep the id of the old skill set the name came from (first by id for merges)
    skill_id = {}
    for n, sid in sorted(old_skill_ids.items(), key=lambda x: int(x[1])):
        skill_id.setdefault(rename.get(n, n), sid)
    next_sid = max(int(s) for s in old_skill_ids.values()) + 1
    for v in rows.values():
        if v["skill_set"] not in skill_id:
            skill_id[v["skill_set"]] = str(next_sid)
            next_sid += 1
    # buckets: a head and its aliases; keep the head's old bucket id when still free
    groups = defaultdict(list)
    for k in rows:
        groups[head_of.get(k, k)].append(k)
    used, bucket_id = set(), {}
    next_bid = max(int(r["bucket_id"]) for r in state) + 1
    for h in sorted(groups, key=lambda h: (-len(groups[h]), int(h))):
        b = old[h]["bucket_id"] if h in old else None
        if b is None or b in used:
            b = str(next_bid)
            next_bid += 1
        used.add(b)
        bucket_id[h] = b
    out = []
    for k, v in rows.items():
        h = head_of.get(k, k)
        r = dict(old[k]) if k in old else {
            "keyword_id": k, "name": v["name"], "verified": str(kws[k]["is_verified"]), "users": kws[k]["users"],
            "decision": "", "matched_keyword": "", "reason": "", "batch": "", "model": ""}
        b = before.get(k)
        changed = b is None or rename.get(b["skill_set"], b["skill_set"]) != v["skill_set"] \
            or b["alias_of"] != (rows[h]["name"] if h != k else "")
        r.update(bucket_id=bucket_id[h], bucket_head=rows[h]["name"], skill_id=skill_id[v["skill_set"]],
                 skill_name=v["skill_set"], suggested="1" if v["suggested"] else "")
        if changed:
            r.update(decision="curated", model="human", batch=batch_no,
                     matched_keyword=rows[h]["name"] if h != k else "",
                     reason=v["comment"] or "curated in workbook")
        elif v["comment"]:
            r["reason"] = v["comment"]
        out.append(r)
    pos = {r["keyword_id"]: i for i, r in enumerate(state)}  # keep the file order; added keywords last
    out.sort(key=lambda r: pos.get(r["keyword_id"], len(pos)))
    return out


def report(res: Result, src: Path, applied: bool) -> str:
    lines = [f"# Curation import: {src.name}", "",
             f"{dt.datetime.now():%Y-%m-%d %H:%M}, {'applied' if applied else 'checked only (nothing written)'}", ""]
    if res.errors:
        lines += [f"## Errors ({len(res.errors)}), fix these in the workbook", ""] + [f"- {e}" for e in res.errors] + [""]
    if res.warnings:
        lines += [f"## Warnings ({len(res.warnings)})", ""] + [f"- {w}" for w in res.warnings] + [""]
    if not res.errors:
        total = sum(len(v) for v in res.changes.values())
        lines += [f"## Changes ({total})", ""]
        if not total:
            lines.append("No changes.")
        for title, items in res.changes.items():
            lines += [f"### {title} ({len(items)})", ""] + [f"- {i}" for i in items] + [""]
    return "\n".join(lines) + "\n"


def cmd_import(args, apply: bool) -> int:
    src = Path(args.file)
    wb = load_workbook(src, data_only=True, read_only=True)
    if KW_SHEET not in wb.sheetnames:
        sys.exit(f"{src} has no '{KW_SHEET}' sheet")
    state = buckets.read_state()
    kws = skills.load_keywords()
    new_state, res = curate(state, kws, read_sheet(wb, KW_SHEET), read_sheet(wb, SET_SHEET))
    return finish(res, new_state, src, apply)


def finish(res: Result, new_state: list[dict], src: Path, apply: bool) -> int:
    do_apply = apply and not res.errors
    text = report(res, src, do_apply)
    OUT.mkdir(parents=True, exist_ok=True)
    rep = OUT / f"{src.stem}_{'applied' if do_apply else 'check'}_{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    rep.write_text(text, encoding="utf-8")
    if do_apply:
        buckets.write_state(new_state)
        buckets.export_marketable(new_state)
    summary = ", ".join(f"{t.lower()} {len(v)}" for t, v in res.changes.items()) or "no changes"
    print(f"{len(res.errors)} errors, {len(res.warnings)} warnings; {summary}")
    for e in res.errors[:15]:
        print(f"  ERROR {e}")
    print(f"Report: {rep.relative_to(skills.ROOT)}")
    if apply and res.errors:
        print("Nothing applied; fix the errors and run again.")
    elif do_apply:
        print(f"Wrote {buckets.STATE.relative_to(skills.ROOT)} and {buckets.MARKETABLE.relative_to(skills.ROOT)}")
    return 1 if res.errors else 0


# ---------- AI layer -> taxonomy ----------

AI_PROPOSAL = skills.AI / "ai_skillset_proposal.csv"
AI_MAPPING = skills.AI / "topic_mapping.csv"


def ai_sheet(state: list[dict], kws: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Keywords-sheet rows with the AI proposal applied (proposal wins for topics with a skill_set).

    For each topic in ai_skillset_proposal.csv with a `skill_set`: its keywords in topic_mapping.csv
    with relation synonym or narrower (and the proposed keyword) move to that skill set with their
    whole alias bucket; synonyms become aliases of the proposed keyword, which heads its bucket and
    is the skill set's only suggested keyword. Products and related keywords are not moved.
    """
    view = current_view(state)
    name = {r["keyword_id"]: r["name"] for r in state}
    rows = {k: {"keyword_id": k, "keyword": name[k], **v, "comment": ""} for k, v in view.items()}
    by_syn = {k["master_synonym_id"]: kid for kid, k in kws.items()}
    by_name = {k["master_synonym"].casefold(): kid for kid, k in kws.items()}
    notes = []

    def head(k):
        return rows[k]["alias_of"] or rows[k]["keyword"]

    for p in skills.read_csv(AI_PROPOSAL):
        sset = norm(p.get("skill_set"))
        if not sset:
            continue
        tid, pk = p["topic_id"], p["keyword_id"]
        if pk not in kws:
            notes.append(f"{tid}: proposed keyword id {pk} unknown; skipped")
            continue
        rel = {}
        for m in skills.read_csv(AI_MAPPING):
            if m["kind"] == "skill" and f"T{int(m['topic_id']):02d}" == tid and m["relation"] in ("synonym", "narrower"):
                k = by_syn.get(m["cinode_id"]) or by_name.get(m["cinode_name"].casefold())
                if k:
                    rel[k] = m["relation"]
        rel[pk] = "synonym"
        for k in rel:
            if k not in rows:
                rows[k] = {"keyword_id": k, "keyword": kws[k]["master_synonym"], "skill_set": sset, "alias_of": "",
                           "suggested": "", "comment": f"added from AI topic {tid}"}
        heads = {head(k) for k in rel}
        moved = [k for k in rows if head(k) in heads]
        for k in moved:
            rows[k]["skill_set"] = sset
        pname = rows[pk]["keyword"]
        syn_heads = {head(k) for k, r in rel.items() if r == "synonym"}
        for k in moved:
            if head(k) in syn_heads:
                rows[k]["alias_of"] = "" if k == pk else pname
        for r in rows.values():
            if r["skill_set"] == sset:
                r["suggested"] = "x" if r["keyword_id"] == pk else ""
        for k in moved:
            rows[k]["comment"] = rows[k]["comment"] or f"AI topic {tid} ({p.get('topic', '')})"
        notes.append(f"{tid} -> skill set '{sset}', suggested '{pname}', {len(moved)} keywords")
    return [{"_row": i, **r} for i, r in enumerate(rows.values(), 2)], notes


def cmd_ai_sync(args) -> int:
    state = buckets.read_state()
    kws = skills.load_keywords()
    kw_rows, notes = ai_sheet(state, kws)
    set_rows = [{"_row": 0, "skill_set": n} for n in dict.fromkeys(norm(r["skill_name"]) for r in state)]
    new_state, res = curate(state, kws, kw_rows, set_rows)
    if not res.errors:
        res.changes = {"AI topics synced": notes, **res.changes}
    return finish(res, new_state, AI_PROPOSAL, args.apply)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="write the curation workbook")
    e.add_argument("--out", help="path (default data/curation/curation_<date>.xlsx)")
    for name in ("check", "apply"):
        s = sub.add_parser(name, help=f"{name} an edited workbook")
        s.add_argument("file")
    a = sub.add_parser("ai-sync", help="move AI topics with a skill_set in ai_skillset_proposal.csv into the taxonomy")
    a.add_argument("--apply", action="store_true", help="write the result (default: report only)")
    args = p.parse_args()
    if args.cmd == "ai-sync":
        return cmd_ai_sync(args)
    if args.cmd == "export":
        cmd_export(args)
        return 0
    return cmd_import(args, apply=args.cmd == "apply")


if __name__ == "__main__":
    sys.exit(main())
