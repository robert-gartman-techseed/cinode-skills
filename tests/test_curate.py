"""Offline tests for src/curate.py: workbook round trip, validation and diff."""
import sys
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import buckets  # noqa: E402
import curate  # noqa: E402

# keyword_id, name, users, bucket_id, skill_id, skill_name, suggested
ROWS = [("1", "Python", 9, "1", "1", "Python", "1"), ("2", "Python3", 3, "1", "1", "Python", ""),
        ("3", "NumPy", 2, "2", "1", "Python", ""), ("4", "SQL", 8, "3", "2", "SQL", "1"),
        ("5", "T-SQL", 4, "4", "2", "SQL", ""), ("6", "Mentoring", 5, "5", "3", "Coaching", "1")]
KWS = {k: {"keyword_id": k, "master_synonym": n, "is_verified": True, "users": u, "type_name": "", "syn_list": []}
       for k, n, u, *_ in ROWS}
KWS["9"] = {"keyword_id": "9", "master_synonym": "Pandas", "is_verified": False, "users": 1, "type_name": "",
            "syn_list": []}


def state():
    heads = {"1": "Python", "2": "NumPy", "3": "SQL", "4": "T-SQL", "5": "Mentoring"}
    return [{"keyword_id": k, "name": n, "verified": "True", "users": str(u), "bucket_id": b, "bucket_head": heads[b],
             "decision": "new", "matched_keyword": "", "reason": "", "batch": "3", "model": "m", "skill_id": s,
             "skill_name": sn, "suggested": sug} for k, n, u, b, s, sn, sug in ROWS]


def sheet(st, edits=None, extra=()):
    view = curate.current_view(st)
    rows = []
    for i, r in enumerate(st, 2):
        v = dict(view[r["keyword_id"]])
        v.update((edits or {}).get(r["name"], {}))
        rows.append({"_row": i, "keyword_id": int(r["keyword_id"]), "keyword": r["name"], **v})
    for j, e in enumerate(extra, len(rows) + 2):
        rows.append({"_row": j, **e})
    return rows


def sets(st, renames=None):
    names = dict.fromkeys(r["skill_name"] for r in st)
    return [{"_row": i, "skill_set": n, "rename_to": (renames or {}).get(n)} for i, n in enumerate(names, 2)]


def run(edits=None, renames=None, extra=()):
    st = state()
    return curate.curate(st, KWS, sheet(st, edits, extra), sets(st, renames))


def test_unchanged_sheet_has_no_changes():
    new, res = run()
    assert not res.errors and not res.warnings and not res.changes
    assert {r["keyword_id"]: r["bucket_id"] for r in new} == {r["keyword_id"]: r["bucket_id"] for r in state()}


def test_alias_move_split_suggest_and_diff():
    new, res = run({"T-SQL": {"alias_of": "sql"},  # case-insensitive
                    "NumPy": {"skill_set": "Data Science", "suggested": "x", "comment": "buyers search it"},
                    "Python3": {"alias_of": ""}})
    assert not res.errors, res.errors
    by = {r["name"]: r for r in new}
    assert by["T-SQL"]["bucket_id"] == by["SQL"]["bucket_id"] and by["T-SQL"]["bucket_head"] == "SQL"
    assert by["Python3"]["bucket_id"] != by["Python"]["bucket_id"] and by["Python3"]["decision"] == "curated"
    assert by["NumPy"]["skill_name"] == "Data Science" and by["NumPy"]["skill_id"] == "4"  # new id
    assert by["NumPy"]["suggested"] == "1" and by["NumPy"]["reason"] == "buyers search it"
    assert by["Python"]["decision"] == "new"  # untouched rows keep their history
    assert set(res.changes) == {"Became an alias", "Moved to another skill set", "Split off (stands on its own)",
                                "Suggested", "Skill sets created"}


def test_rename_and_merge_skill_sets():
    new, res = run(renames={"SQL": "Databases", "Coaching": "Databases"})
    assert not res.errors
    assert {r["skill_name"] for r in new} == {"Python", "Databases"}
    assert {r["skill_id"] for r in new if r["skill_name"] == "Databases"} == {"2"}
    assert res.changes["Skill sets merged"] == ["Coaching + SQL -> Databases"]


def test_errors_block_the_import():
    _, res = run({"T-SQL": {"alias_of": "Python"},        # alias across skill sets
                  "Python3": {"suggested": "x"},          # suggested alias
                  "NumPy": {"alias_of": "Numpyy"},        # unknown target
                  "Mentoring": {"skill_set": ""}})        # no skill set
    assert len(res.errors) == 4 and "Became an alias" not in res.changes
    assert "did you mean 'NumPy'" in " ".join(res.errors)


def test_deleted_row_and_unknown_keyword_are_errors():
    st = state()
    rows = sheet(st)[1:] + [{"_row": 99, "keyword_id": "12345", "skill_set": "SQL"}]
    _, res = curate.curate(st, KWS, rows, sets(st))
    assert any("missing" in e for e in res.errors) and any("12345" in e for e in res.errors)


def test_alias_chain_resolves_to_root_and_loops_fail():
    new, res = run({"Python3": {"alias_of": "NumPy"}, "NumPy": {"alias_of": "Python"}})
    assert not res.errors and any("itself an alias" in w for w in res.warnings)
    assert len({r["bucket_id"] for r in new if r["skill_id"] == "1"}) == 1
    _, res = run({"Python": {"alias_of": "Python3"}, "Python3": {"alias_of": "Python"}})
    assert any("loop" in e for e in res.errors)


def test_add_keyword_from_catalog_and_typo_warning():
    new, res = run({"Mentoring": {"skill_set": "Pythn"}},
                   extra=[{"keyword_id": 9.0, "keyword": "Pandas", "skill_set": "Python", "alias_of": ""}])
    assert not res.errors
    assert any("typo for 'Python'" in w for w in res.warnings)
    assert next(r for r in new if r["name"] == "Pandas")["skill_id"] == "1"


def test_workbook_round_trip(tmp_path, monkeypatch):
    st = state()
    monkeypatch.setattr(buckets, "read_state", lambda: st)
    monkeypatch.setattr(curate.skills, "load_keywords", lambda: KWS)
    path = tmp_path / "c.xlsx"
    curate.cmd_export(type("A", (), {"out": str(path)}))
    wb = load_workbook(path)
    ws = wb[curate.KW_SHEET]
    col = {c.value: c.column for c in ws[1]}
    row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, col["keyword"]).value == "T-SQL")
    ws.cell(row, col["alias_of"]).value = "SQL"
    wb.save(path)
    wb = load_workbook(path, data_only=True)
    new, res = curate.curate(st, KWS, curate.read_sheet(wb, curate.KW_SHEET), curate.read_sheet(wb, curate.SET_SHEET))
    assert not res.errors and list(res.changes) == ["Became an alias"]
