"""Run: python -m unittest -v. PDF and live GUI checks skip if unavailable."""
import importlib.util
from contextlib import closing
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from export import export_html, export_pdf
from storage import Project, SCHEMA, scan_code


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.project = Project(self.folder / "test.reqtrace", create=True)
        self.system = self.project.add_document("System", "SYS")
        self.software = self.project.add_document("Software", "EBBSYS")

    def tearDown(self):
        self.project.close()
        self.temp.cleanup()

    def add(self, document, title="Read sensor", body="The software shall read the sensor."):
        return self.project.save_requirement(document, None, title, body)

    def test_ids_survive_deletion_and_reopening(self):
        self.assertEqual(self.add(self.software), "EBBSYS1")
        self.project.delete_requirement("EBBSYS1")
        path = self.project.path
        self.project.close()
        self.project = Project(path)
        self.assertEqual(self.add(self.software), "EBBSYS2")

    def test_overlapping_prefixes_cannot_recycle_or_collide(self):
        for number in range(1, 13):
            self.add(self.software, f"Requirement {number}")
        self.project.delete_requirement("EBBSYS11")
        other = self.project.add_document("Other", "EBBSYS1")
        self.assertEqual(self.add(other), "EBBSYS13")
        self.assertEqual(self.add(self.software, "Next requirement"), "EBBSYS14")

    def test_duplicate_content_rejected_without_consuming_id(self):
        self.add(self.software, " Read SENSOR ", "Read   data")
        with self.assertRaises(ValueError):
            self.add(self.software, "read sensor", "read data")
        self.assertEqual(self.add(self.software, "Other", "Other text"), "EBBSYS2")
        with self.assertRaises(ValueError):
            self.project.save_requirement(self.software, "EBBSYS2", "read sensor", "read data")
        self.assertEqual(self.project.requirements(self.software)[1]["title"], "Other")
        self.project.save_requirement(self.software, "EBBSYS1", "Changed", "Changed text")
        self.assertEqual(self.project.requirements(self.software)[0]["id"], "EBBSYS1")

    def test_document_prefix_validation(self):
        for prefix in ("EBBSYS", "ebbsys", "1ABC", "BAD SPACE", ""):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                self.project.add_document("New", prefix)
        self.assertEqual(self.project.add_document("Low level", "sw_ll-"), 3)

    def test_modules_have_independent_prefixes_and_counters(self):
        doc = self.project.add_document("Combined software", "SHLRSCM", "SCM")
        sections = [self.project.sections(doc)[0]["id"]]
        sections += [self.project.add_section(doc, module, "SHLR" + module) for module in ("IOM", "DDM")]
        for section, prefix in zip(sections, ("SHLRSCM", "SHLRIOM", "SHLRDDM")):
            for number in (1, 2):
                result = self.project.save_requirement(doc, None, f"{prefix} requirement {number}", "", section, "Functional")
                self.assertEqual(result, prefix + str(number))
        self.assertEqual(len(self.project.requirements(doc)), 6)

    def test_section_validation_and_document_creation_are_atomic(self):
        self.project.add_section(self.software, "IOM", "SHLRIOM")
        for document, name, prefix in ((self.software, "iom", "NEW"), (self.system, "Other", "shlriom"),
                                       (self.software, "", "NEW"), (999, "Missing", "NEW")):
            with self.subTest(name=name, prefix=prefix), self.assertRaises(ValueError):
                self.project.add_section(document, name, prefix)
        with self.assertRaises(ValueError):
            self.project.add_document("Must roll back", "SHLRIOM")
        self.assertEqual(len(self.project.documents()), 2)
        self.project.add_section(self.system, "IOM", "SYSIOM")

    def test_section_move_keeps_id_links_code_and_both_counters(self):
        parent, requirement = self.add(self.system), self.add(self.software)
        iom = self.project.add_section(self.software, "IOM", "SHLRIOM")
        self.project.link(parent, requirement)
        self.project.replace_code_refs([(requirement, "source.py", 7)])
        result = self.project.save_requirement(self.software, requirement, "Moved", "Text", iom, " Usability ")
        self.assertEqual(result, "EBBSYS1")
        row = self.project.requirements(self.software, iom, "Usability")[0]
        self.assertEqual((row["section_name"], row["section_prefix"]), ("IOM", "SHLRIOM"))
        self.assertEqual(self.project.types(self.software), ["Usability"])
        self.project.save_requirement(self.software, requirement, "Edited", "Text")
        row = next(r for r in self.project.matrix() if r["id"] == requirement)
        self.assertEqual((row["section"], row["type"], row["parents"], row["code"]),
                         (iom, "Usability", [parent], ["source.py:7"]))
        self.assertEqual(self.project.save_requirement(self.software, None, "IOM new", "", iom), "SHLRIOM1")
        self.assertEqual(self.add(self.software, "General new"), "EBBSYS2")
        with self.assertRaises(ValueError):
            self.project.save_requirement(self.system, parent, "Invalid move", "", iom)

    def test_delete_section_moves_every_requirement_and_keeps_ids_and_links(self):
        general = self.project.sections(self.software)[0]["id"]
        iom = self.project.add_section(self.software, "IOM", "SHLRIOM")
        first = self.project.save_requirement(self.software, None, "Functional", "Text", general, "Functional")
        second = self.project.save_requirement(self.software, None, "Usability", "Other text", general, "Usability")
        deleted = self.add(self.software, "Retired")
        self.project.delete_requirement(deleted)
        parent = self.add(self.system)
        self.project.link(parent, first)
        self.project.link(parent, second)
        self.project.replace_code_refs([(first, "source.py", 7)])
        before = {r["id"]: r for r in self.project.matrix()}
        self.assertEqual(self.project.delete_section(self.software, general, iom), 2)
        after = {r["id"]: r for r in self.project.matrix()}
        for requirement in (first, second):
            self.assertEqual(after[requirement]["section"], iom)
            for field in ("id", "title", "description", "type", "parents", "children", "code"):
                self.assertEqual(after[requirement][field], before[requirement][field])
        self.assertEqual([s["id"] for s in self.project.sections(self.software)], [iom])
        self.assertEqual(self.project.save_requirement(self.software, None, "New IOM", "", iom), "SHLRIOM1")
        new_doc = self.project.add_document("Reused prefix", "EBBSYS")
        self.assertEqual(self.add(new_doc), "EBBSYS4")
        self.assertEqual(list(self.project.db.execute("PRAGMA foreign_key_check")), [])

    def test_delete_empty_section_retains_deleted_ids(self):
        general = self.project.sections(self.software)[0]["id"]
        self.project.add_section(self.software, "IOM", "SHLRIOM")
        requirement = self.add(self.software)
        self.project.delete_requirement(requirement)
        self.assertEqual(self.project.delete_section(self.software, general), 0)
        tombstone = self.project.db.execute("SELECT * FROM requirements WHERE id=?", (requirement,)).fetchone()
        self.assertEqual(tombstone["deleted"], 1)
        self.assertIsNone(tombstone["section"])
        recreated = self.project.add_section(self.software, "General", "EBBSYS")
        self.assertEqual(self.project.save_requirement(self.software, None, "New", "", recreated), "EBBSYS2")

    def test_invalid_section_deletion_preserves_data(self):
        general = self.project.sections(self.software)[0]["id"]
        self.add(self.software)
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.project.delete_section(self.software, general)
        iom = self.project.add_section(self.software, "IOM", "SHLRIOM")
        foreign = self.project.sections(self.system)[0]["id"]
        before = self.project.matrix()
        for section, target in ((general, None), (general, general), (general, foreign), (general, 999),
                                (foreign, iom), (999, iom)):
            with self.subTest(section=section, target=target), self.assertRaises(ValueError):
                self.project.delete_section(self.software, section, target)
            self.assertEqual(self.project.matrix(), before)
            self.assertEqual(len(self.project.sections(self.software)), 2)

    def test_section_delete_failure_rolls_back_moves_and_prefix_change(self):
        general = self.project.sections(self.software)[0]["id"]
        iom = self.project.add_section(self.software, "IOM", "SHLRIOM")
        self.add(self.software)
        before = self.project.matrix()
        self.project.db.execute("CREATE TRIGGER reject_section_delete BEFORE DELETE ON sections "
                                "BEGIN SELECT RAISE(ABORT, 'Simulated delete failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.project.delete_section(self.software, general, iom)
        self.assertEqual(self.project.matrix(), before)
        self.assertEqual(self.project.documents()[1]["prefix"], "EBBSYS")
        self.assertEqual(len(self.project.sections(self.software)), 2)

    def test_filters_and_duplicates_span_sections_within_document(self):
        general = self.project.sections(self.software)[0]["id"]
        iom = self.project.add_section(self.software, "IOM", "SHLRIOM")
        self.project.save_requirement(self.software, None, "General functional", "", general, "Functional")
        typed = self.project.save_requirement(self.software, None, "IOM usability", "", iom, "Usability")
        untyped = self.project.save_requirement(self.software, None, "IOM untyped", "", iom, "")
        self.assertEqual([r["id"] for r in self.project.requirements(self.software, iom, "Usability")], [typed])
        self.assertEqual([r["id"] for r in self.project.requirements(self.software, requirement_type="")], [untyped])
        self.assertEqual(len(self.project.requirements(self.software, iom)), 2)
        with self.assertRaises(ValueError):
            self.project.save_requirement(self.software, None, "IOM usability", "", general, "Functional")

    def test_v1_migration_preserves_ids_links_refs_and_reserved_numbers(self):
        path = self.folder / "legacy.reqtrace"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(SCHEMA)
            db.executemany("INSERT INTO documents(id,name,prefix,next_number) VALUES (?,?,?,?)",
                           [(1, "System", "SYS", 2), (2, "Software", "SHLRSCM", 4)])
            for requirement, document, deleted in (("SYS1", 1, 0), ("SHLRSCM1", 2, 0), ("SHLRSCM3", 2, 1)):
                db.execute("INSERT INTO requirements VALUES (?,?,?,?,?,?)",
                           (requirement, document, requirement, "Original text",
                            repr((requirement.casefold(), "original text")), deleted))
            db.execute("INSERT INTO links VALUES ('SYS1','SHLRSCM1')")
            db.execute("INSERT INTO code_refs VALUES ('SHLRSCM1','source.py',12)")
        legacy = Project(path)
        try:
            self.assertEqual(legacy.db.execute("PRAGMA user_version").fetchone()[0], 2)
            section = legacy.sections(2)[0]
            self.assertEqual((section["name"], section["prefix"], section["next_number"]), ("General", "SHLRSCM", 4))
            row = legacy.matrix()[1]
            self.assertEqual((row["id"], row["title"], row["description"], row["type"]),
                             ("SHLRSCM1", "SHLRSCM1", "Original text", ""))
            self.assertEqual((row["parents"], row["code"]), (["SYS1"], ["source.py:12"]))
            with self.assertRaises(ValueError):
                legacy.save_requirement(2, None, "SHLRSCM1", "Original text")
            self.assertEqual(legacy.save_requirement(2, None, "Next", ""), "SHLRSCM4")
            legacy.close()
            legacy = Project(path)
            self.assertEqual(len(legacy.sections()), 2)
            self.assertEqual(legacy.save_requirement(2, None, "After reopen", ""), "SHLRSCM5")
        finally:
            legacy.close()

    def test_failed_migration_rolls_back_all_schema_changes(self):
        path = self.folder / "inconsistent.reqtrace"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(SCHEMA)
            db.execute("ALTER TABLE requirements ADD COLUMN type TEXT")
        with self.assertRaises(sqlite3.Error):
            Project(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertNotIn("section", [r[1] for r in db.execute("PRAGMA table_info(requirements)")])
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='sections'").fetchone())

    def test_html_groups_document_section_and_custom_type_with_escaping(self):
        iom = self.project.add_section(self.software, "IOM <module>", "SHLRIOM")
        self.project.save_requirement(self.software, None, "IOM usability", "", iom, "Usability & access")
        self.add(self.software, "General requirement")
        output = self.folder / "grouped.html"
        export_html(output, "Project", list(reversed(self.project.matrix())))
        text = output.read_text()
        self.assertIn("Software / IOM &lt;module&gt; [SHLRIOM] / Usability &amp; access", text)
        self.assertIn("Software / General [EBBSYS] / Unspecified", text)
        self.assertEqual(text.count("scope='rowgroup'"), 2)
        self.assertLess(text.index("EBBSYS1"), text.index("SHLRIOM1"))

    def test_bidirectional_links_cycles_and_deletion_cleanup(self):
        parent, child = self.add(self.system), self.add(self.software)
        low_doc = self.project.add_document("Low level", "LL")
        low = self.add(low_doc)
        self.project.link(parent, child)
        self.project.link(child, low)
        for pair in ((parent, child), (child, parent), (low, parent), (child, child)):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                self.project.link(*pair)
        rows = {r["id"]: r for r in self.project.matrix()}
        self.assertEqual(rows[parent]["children"], [child])
        self.assertEqual(rows[child]["parents"], [parent])
        self.project.unlink(child, low)
        self.assertEqual(self.project.matrix()[2]["parents"], [])
        self.project.replace_code_refs([(child, "source.py", 5)])
        self.project.delete_requirement(child)
        self.assertEqual(self.project.matrix()[0]["children"], [])
        self.assertEqual(list(self.project.db.execute("SELECT * FROM code_refs")), [])

    def test_scan_locations_unknown_tags_and_replacement(self):
        requirement = self.add(self.software)
        code = self.folder / "source.py"
        code.write_text("# REQ: ebbsys1 REQ: EBBSYS1\n# REQ: UNKNOWN9\n", encoding="utf-8")
        ignored = self.folder / "node_modules"
        ignored.mkdir()
        (ignored / "ignore.py").write_text("# REQ: IGNORED1")
        (self.folder / "bad.py").write_bytes(b"\xff\xfe")
        (self.folder / "huge.py").write_bytes(b"#" * 2_000_001)
        refs, unknown, skipped = scan_code(self.folder, {requirement})
        self.assertEqual(refs, [(requirement, str(code), 1)])
        self.assertEqual(unknown, [f"{code}:2: UNKNOWN9"])
        self.assertEqual(len(skipped), 2)
        self.project.replace_code_refs(refs)
        self.assertEqual(self.project.matrix()[0]["code"], [f"{code}:1"])
        code.write_text("# Tag removed\n")
        self.project.replace_code_refs(scan_code(self.folder, {requirement})[0])
        self.assertEqual(self.project.matrix()[0]["code"], [])
        with self.assertRaises(ValueError):
            scan_code(self.folder / "missing", {requirement})

    def test_failed_reference_update_preserves_previous_scan(self):
        requirement = self.add(self.software)
        refs = [(requirement, "valid.py", 1)]
        self.project.replace_code_refs(refs)
        with self.assertRaises(sqlite3.IntegrityError):
            self.project.replace_code_refs([("MISSING1", "bad.py", 2)])
        self.assertEqual([tuple(r) for r in self.project.db.execute("SELECT * FROM code_refs")], refs)

    def test_html_escapes_content_and_reports_coverage(self):
        self.add(self.software, '<script>alert("hi")</script>', "A & B ≤ 5\nSecond line")
        output = self.folder / "matrix.html"
        export_html(output, "Project <name>", self.project.matrix())
        text = output.read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)
        for expected in ("&lt;script&gt;", "Project &lt;name&gt;", "A &amp; B ≤ 5<br>Second line",
                         "EBBSYS1", "No requirement links", "No code tags"):
            self.assertIn(expected, text)
        export_html(output, "Empty", [])
        self.assertIn("0 requirements", output.read_text())

    def test_invalid_open_and_overwrite_leave_files_untouched(self):
        invalid = self.folder / "notes.txt"
        invalid.write_text("Do not change this file.")
        with self.assertRaises((ValueError, sqlite3.Error)):
            Project(invalid)
        self.assertEqual(invalid.read_text(), "Do not change this file.")
        with self.assertRaises(ValueError):
            Project(self.project.path, create=True)
        with self.assertRaises(sqlite3.Error):
            Project(self.folder / "missing.reqtrace")
        self.assertFalse((self.folder / "missing.reqtrace").exists())

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "Install requirements.txt for PDF check")
    def test_pdf_empty_and_long_rows_paginate(self):
        output = self.folder / "matrix.pdf"
        export_pdf(output, "Empty", [])
        self.assertTrue(output.read_bytes().startswith(b"%PDF-"))
        self.add(self.software, "Long requirement <&>", "Read café sensor data ≤ 5. " * 1800)
        export_pdf(output, "Long project", self.project.matrix())
        pdf = output.read_bytes()
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        self.assertGreater(len(re.findall(rb"/Type\s*/Page\b", pdf)), 1)

    def test_missing_pdf_dependency_gives_setup_instructions(self):
        output = self.folder / "missing.pdf"
        with patch.dict("sys.modules", {"reportlab": None}):
            with self.assertRaisesRegex(ValueError, "pip install -r requirements.txt"):
                export_pdf(output, "Project", [])
        self.assertFalse(output.exists())


class ControllerTests(unittest.TestCase):
    def test_cancel_filter_change_restores_both_filters(self):
        from views import RequirementsView
        state = SimpleNamespace(section_filter=Mock(), type_filter=Mock(), enabled=True,
                                active_filters=("All sections", "All types"), confirm_edits=lambda: False,
                                section_box=Mock(), type_box=Mock(),
                                section_filters={"All sections": None}, type_filters={"All types": None})
        state.show_filters = lambda: RequirementsView.show_filters(state)
        state.section_filter.get.return_value = "IOM [SHLRIOM]"
        state.type_filter.get.return_value = "Type: Usability"
        RequirementsView.change_filters(state)
        state.section_filter.set.assert_called_once_with("All sections")
        state.type_filter.set.assert_called_once_with("All types")

    def test_saved_requirement_outside_filters_is_revealed(self):
        from views import RequirementsView
        with TemporaryDirectory() as folder:
            project = Project(Path(folder) / "filters.reqtrace", create=True)
            try:
                doc = project.add_document("Software", "SHLRSCM", "SCM")
                scm = project.sections(doc)[0]["id"]
                iom = project.add_section(doc, "IOM", "SHLRIOM")
                requirement = project.save_requirement(doc, None, "Moved", "", iom, "Usability")
                state = SimpleNamespace(project_path=project.path, document=doc, tree=Mock(),
                                        show_filters=Mock(), render=Mock(), select=Mock(),
                                        active_filters=("SCM [SHLRSCM]", "Type: Functional"))
                state.defaults = lambda: RequirementsView.defaults(state)
                RequirementsView.refresh(state, project, doc, requirement)
                self.assertEqual(state.active_filters, ("All sections", "All types"))
                self.assertEqual(state.selected, requirement)
                state.select.assert_called_once_with(requirement, reveal=True)
            finally:
                project.close()

    def test_save_during_navigation_keeps_the_requested_selection(self):
        from app import App
        state = SimpleNamespace(requirement="SYS1", busy=False, document=1, requirements_view=Mock(),
                                project=Mock(), docs=Mock(), refresh_requirements=Mock())
        state.project.requirements.return_value = [{"id": "SYS1", "document": 1}, {"id": "SYS2", "document": 1}]

        def save_current():
            state.requirement = "SYS1"
            return True

        state.confirm_edits = save_current
        self.assertTrue(App.select_requirement(state, "SYS2"))
        state.refresh_requirements.assert_called_once_with("SYS2")

    def test_cancel_navigation_keeps_current_requirement(self):
        from app import App
        state = SimpleNamespace(requirement="SYS1", busy=False,
                                confirm_edits=lambda: False, refresh_requirements=Mock())
        self.assertFalse(App.select_requirement(state, "SYS2"))
        self.assertEqual(state.requirement, "SYS1")
        state.refresh_requirements.assert_not_called()

    def test_live_gui_create_save_switch_and_reopen(self):
        import tkinter as tk
        from app import App
        try:
            probe = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Desktop display unavailable: {error}")
        probe.destroy()
        app = App()
        callback_errors = []
        app.report_callback_exception = lambda *error: callback_errors.append(error)
        with TemporaryDirectory() as folder, patch("app.messagebox.askyesnocancel", return_value=True):
            path = Path(folder) / "gui.reqtrace"
            app.project = Project(path, create=True)
            try:
                doc = app.project.add_document("Software", "EBBSYS")
                app.docs.insert("", "end", iid=str(doc), values=("Software",))
                app.docs.selection_set(str(doc))
                app.update()
                app.title_text.set("Read sensor")
                app.body.insert("1.0", "The software shall read a sensor.")
                app.save()
                app.new_requirement()
                app.title_text.set("Report sensor")
                app.save()
                app.title_text.set("Report updated sensor")
                app.requirements_view.tree.selection_set("EBBSYS1")
                app.update()
                self.assertEqual(app.requirement, "EBBSYS1")
                self.assertEqual(app.project.requirements(doc)[1]["title"], "Report updated sensor")
                iom = app.project.add_section(doc, "IOM", "SHLRIOM")
                app.refresh_requirements("EBBSYS1")
                app.section_text.set("IOM [SHLRIOM]")
                app.type_text.set("Usability")
                app.save()
                self.assertEqual(app.requirement, "EBBSYS1")
                app.requirements_view.section_filter.set("IOM [SHLRIOM]")
                app.requirements_view.type_filter.set("Type: Usability")
                app.requirements_view.change_filters()
                app.new_requirement()
                app.title_text.set("New IOM requirement")
                app.save()
                self.assertEqual(app.requirement, "SHLRIOM1")
                self.assertEqual(len(app.project.requirements(doc, iom, "Usability")), 2)
                app.project.close()
                app.project = Project(path)
                self.assertEqual(len(app.project.requirements()), 3)
                system = app.project.add_document("System", "SYS")
                parent = app.project.save_requirement(system, None, "System parent", "")
                app.docs.insert("", "end", iid=str(system), values=("System",))
                app.project.link(parent, "EBBSYS1")
                app.refresh_requirements("EBBSYS1")
                app.notebook.select(app.matrix_view)
                app.update()
                self.assertEqual(len(app.matrix_view.nodes), 4)
                self.assertEqual(len(app.matrix_view.lines), 1)
                app.matrix_view.choose(parent)
                app.update()
                self.assertEqual(app.requirement, parent)
                app.title_text.set("Unsaved system edit")
                with patch("app.messagebox.askyesnocancel", return_value=None):
                    app.matrix_view.choose("EBBSYS1")
                    self.assertEqual(app.requirement, parent)
                    self.assertEqual(app.matrix_view.selected, parent)
                    self.assertEqual(app.title_text.get(), "Unsaved system edit")
                app.matrix_view.choose("EBBSYS1")
                app.update()
                self.assertEqual(app.requirement, "EBBSYS1")
                self.assertEqual(app.project.requirements(system)[0]["title"], "Unsaved system edit")
                app.title_text.set("Draft survives tab switch")
                app.notebook.select(app.requirements_view)
                app.update()
                group = f"section:{iom}"
                app.requirements_view.tree.item(group, open=False)
                app.requirements_view.sort("Title")
                self.assertFalse(app.requirements_view.tree.item(group, "open"))
                app.requirements_view.tree.selection_set(group)
                app.update()
                self.assertEqual(app.title_text.get(), "Draft survives tab switch")
                self.assertEqual(app.requirement, "EBBSYS1")
                app.notebook.select(app.matrix_view)
                app.update()
                self.assertEqual(app.title_text.get(), "Draft survives tab switch")
                app.requirements_view.tree.selection_set(group)
                app.delete_section()
                window = next(w for w in app.winfo_children() if isinstance(w, tk.Toplevel))
                frame = window.winfo_children()[0]
                button = next(w for w in frame.winfo_children() if w.winfo_class() == "TButton"
                              and w.cget("text") == "Delete section")
                with patch("app.messagebox.askyesno", return_value=True):
                    button.invoke()
                app.update()
                self.assertEqual(len(app.project.sections(doc)), 1)
                self.assertEqual(app.requirement, "EBBSYS1")
                self.assertFalse(app.requirements_view.tree.exists(group))
                self.assertEqual(len(app.matrix_view.lines), 1)
                self.assertEqual(callback_errors, [])
            finally:
                app.project.close()
                app.destroy()


if __name__ == "__main__":
    unittest.main()
