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


def test_ai_sync_moves_topic_buckets_and_sets_the_suggested_keyword(tmp_path, monkeypatch):
    kws = {k: {**v, "master_synonym_id": "s" + k} for k, v in KWS.items()}
    prop, mapping = tmp_path / "p.csv", tmp_path / "m.csv"
    prop.write_text("topic_id,topic,skill_set,keyword_id\nT06,Prompting,Context Engineering,5\n"
                    "T07,Other,,1\n", encoding="utf-8")
    mapping.write_text("topic_id,kind,cinode_id,cinode_name,match,relation\n6,skill,s4,SQL,exact,synonym\n"
                       "6,skill,s3,NumPy,exact,narrower\n6,skill,s6,Mentoring,exact,related\n"
                       "6,skill,s9,Pandas,exact,narrower\n", encoding="utf-8")
    monkeypatch.setattr(curate, "AI_PROPOSAL", prop)
    monkeypatch.setattr(curate, "AI_MAPPING", mapping)
    st = state()
    rows, notes = curate.ai_sheet(st, kws)
    new, res = curate.curate(st, kws, rows, sets(st))
    assert not res.errors, res.errors
    by = {r["name"]: r for r in new}
    ce = {n for n, r in by.items() if r["skill_name"] == "Context Engineering"}
    assert ce == {"T-SQL", "SQL", "NumPy", "Pandas"}  # related Mentoring stays; untouched T07
    assert by["SQL"]["bucket_head"] == "T-SQL" and by["T-SQL"]["suggested"] == "1"  # synonym -> alias of proposed
    assert by["NumPy"]["bucket_head"] == "NumPy" and not by["NumPy"]["suggested"]


def test_ai_sync_suggest_no_adds_a_skill_without_taking_the_suggestion(tmp_path, monkeypatch):
    kws = {k: {**v, "master_synonym_id": "s" + k} for k, v in KWS.items()}
    prop, mapping = tmp_path / "p.csv", tmp_path / "m.csv"
    prop.write_text("topic_id,topic,skill_set,suggest,keyword_id\nT09,MCP,SQL,no,6\n", encoding="utf-8")
    mapping.write_text("topic_id,kind,cinode_id,cinode_name,match,relation\n", encoding="utf-8")
    monkeypatch.setattr(curate, "AI_PROPOSAL", prop)
    monkeypatch.setattr(curate, "AI_MAPPING", mapping)
    st = state()
    new, res = curate.curate(st, kws, curate.ai_sheet(st, kws)[0], sets(st))
    by = {r["name"]: r for r in new}
    assert by["Mentoring"]["skill_name"] == "SQL" and not by["Mentoring"]["suggested"]
    assert by["SQL"]["suggested"] == "1"


def test_ai_sync_moves_products_out_and_uses_interim_keywords(tmp_path, monkeypatch):
    """A mapped product sitting in a topic's skill set moves to a product skill set of its own; a
    topic whose new keyword is not in Cinode yet syncs with interim_keyword_id; general topics sync."""
    kws = {k: {**v, "master_synonym_id": "s" + k} for k, v in KWS.items()}
    prop, mapping = tmp_path / "p.csv", tmp_path / "m.csv"
    prop.write_text("topic_id,topic,status,scope,skill_set,keyword_id,interim_keyword_id\n"
                    "T22,Stack,active,ai,Python,,1\nT35,Twins,active,general,Coaching,6,\n"
                    "T31,New,active,ai,Decisions,,\n", encoding="utf-8")
    mapping.write_text("topic_id,kind,cinode_id,cinode_name,match,relation\n22,skill,s3,NumPy,exact,product\n",
                       encoding="utf-8")
    monkeypatch.setattr(curate, "AI_PROPOSAL", prop)
    monkeypatch.setattr(curate, "AI_MAPPING", mapping)
    st = state()
    rows, notes = curate.ai_sheet(st, kws)
    new, res = curate.curate(st, kws, rows, sets(st))
    assert not res.errors, res.errors
    by = {r["name"]: r for r in new}
    assert by["Python"]["skill_name"] == "Python" and by["Python"]["suggested"] == "1"  # interim keyword
    assert by["NumPy"]["skill_name"] == "NumPy"  # product moved out of the Python skill set
    assert by["Mentoring"]["skill_name"] == "Coaching"  # general topic synced
    assert any("T31" in n and "skipped" in n for n in notes)


def _export_and_open(tmp_path, monkeypatch, st, proposal=""):
    prop = tmp_path / "prop.csv"
    prop.write_text(proposal or "topic_id,status,scope,skill_set,cinode_skill_set_id\n", encoding="utf-8")
    monkeypatch.setattr(curate, "AI_PROPOSAL", prop)
    monkeypatch.setattr(curate, "AI_MAPPING", tmp_path / "none.csv")
    monkeypatch.setattr(curate, "EXCLUDED", tmp_path / "excluded.csv")
    monkeypatch.setattr(buckets, "read_state", lambda: st)
    monkeypatch.setattr(curate.skills, "load_keywords", lambda: KWS)
    path = tmp_path / "c.xlsx"
    curate.cmd_export(type("A", (), {"out": str(path)}))
    wb = load_workbook(path)
    ws = wb[curate.KW_SHEET]
    col = {c.value: c.column for c in ws[1]}
    row = {ws.cell(r, col["keyword"]).value: r for r in range(2, ws.max_row + 1)}
    return path, wb, ws, col, row


def _check(path, st):
    wb = load_workbook(path, data_only=True)
    return curate.curate(st, KWS, curate.read_sheet(wb, curate.KW_SHEET), curate.read_sheet(wb, curate.SET_SHEET))


def test_new_workbook_has_instructions_set_info_and_dropdowns(tmp_path, monkeypatch):
    st = state()
    path, wb, ws, col, _ = _export_and_open(tmp_path, monkeypatch, st,
                                            "topic_id,status,scope,skill_set,cinode_skill_set_id\nT99,active,ai,SQL,9999\n")
    assert curate.README_SHEET in wb.sheetnames and wb.sheetnames[0] == "Instructions"
    assert {"new_skill_set", "exclude", "skill_set", "was_skill_set"} <= set(col)
    sets = wb[curate.SET_SHEET]
    scol = {c.value: c.column for c in sets[1]}
    sql = next(r for r in range(2, sets.max_row + 1) if sets.cell(r, scol["skill_set"]).value == "SQL")
    assert sets.cell(sql, scol["scope"]).value == "ai" and sets.cell(sql, scol["cinode_skill_set_id"]).value == "9999"
    dv = {str(d.sqref).split(":")[0][:1]: d for d in ws.data_validations.dataValidation}
    assert dv[ws.cell(1, col["skill_set"]).column_letter].errorStyle == "stop"
    assert ws.freeze_panes == "C2" and ws.auto_filter.ref


def test_round_trip_move_new_set_exclude_and_cinode_plan(tmp_path, monkeypatch):
    st = state()
    path, wb, ws, col, row = _export_and_open(
        tmp_path, monkeypatch, st, "topic_id,status,scope,skill_set,cinode_skill_set_id\nT99,active,ai,SQL,9999\n")
    ws.cell(row["NumPy"], col["skill_set"]).value = "SQL"          # move into a Cinode-backed set
    ws.cell(row["Mentoring"], col["new_skill_set"]).value = "Coaching & Mentoring"  # propose a new set
    ws.cell(row["Python3"], col["exclude"]).value = "x"            # exclude an alias
    ws.cell(row["Python3"], col["alias_of"]).value = ""
    wb.save(path)
    new, res = _check(path, st)
    assert not res.errors, res.errors
    by = {r["name"]: r for r in new}
    assert by["NumPy"]["skill_name"] == "SQL" and by["Mentoring"]["skill_name"] == "Coaching & Mentoring"
    assert "Python3" not in by and res.excluded[0]["name"] == "Python3"
    assert {"Moved to another skill set", "Skill sets created", "Excluded (taken out of the skill sets)"} <= set(res.changes)
    assert res.cinode_plan == [{"cinode_skill_set_id": 9999, "skill_set": "SQL",
                                "add": [{"id": 3, "keyword": "NumPy"}], "remove": []}]
    # apply writes the taxonomy and the excluded list (into tmp files)
    monkeypatch.setattr(buckets, "STATE", tmp_path / "state.csv")
    monkeypatch.setattr(buckets, "MARKETABLE", tmp_path / "m.csv")
    monkeypatch.setattr(curate, "OUT", tmp_path / "out")
    monkeypatch.setattr(curate.skills, "ROOT", tmp_path)
    assert curate.finish(res, new, path, apply=True) == 0
    assert "Python3" in (tmp_path / "excluded.csv").read_text(encoding="utf-8")
    assert "NumPy" in (tmp_path / "state.csv").read_text(encoding="utf-8")


def test_strict_checks_unknown_set_and_mismatched_keyword(tmp_path, monkeypatch):
    st = state()
    path, wb, ws, col, row = _export_and_open(tmp_path, monkeypatch, st)
    ws.cell(row["NumPy"], col["skill_set"]).value = "Pythn"         # unknown name in skill_set
    ws.cell(row["SQL"], col["keyword"]).value = "MySQL"             # keyword edited: id/name mismatch
    ws.cell(row["T-SQL"], col["people"]).value = 999                # read-only cell edited
    ws.cell(row["Python"], col["exclude"]).value = "x"              # excluded head, alias still points at it
    wb.save(path)
    _, res = _check(path, st)
    text = " ".join(res.errors)
    assert "unknown skill set 'Pythn'" in text and "did you mean 'Python'" in text
    assert "do not match" in text and "which is excluded" in text
    assert any("read-only column 'people'" in w for w in res.warnings)
