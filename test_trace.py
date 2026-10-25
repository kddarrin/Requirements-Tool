"""Run: python -m unittest -v. PDF and live GUI checks skip if unavailable."""
import importlib.util
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
        with sqlite3.connect(path) as db:
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
        with sqlite3.connect(path) as db:
            db.executescript(SCHEMA)
            db.execute("ALTER TABLE requirements ADD COLUMN type TEXT")
        with self.assertRaises(sqlite3.Error):
            Project(path)
        with sqlite3.connect(path) as db:
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
        from app import App
        state = SimpleNamespace(section_filter=Mock(), type_filter=Mock(), busy=False,
                                active_filters=("All sections", "All types"), confirm_edits=lambda: False)
        state.section_filter.get.return_value = "IOM [SHLRIOM]"
        state.type_filter.get.return_value = "Type: Usability"
        App.change_filters(state)
        state.section_filter.set.assert_called_once_with("All sections")
        state.type_filter.set.assert_called_once_with("All types")

    def test_saved_requirement_outside_filters_is_revealed(self):
        from app import App
        with TemporaryDirectory() as folder:
            project = Project(Path(folder) / "filters.reqtrace", create=True)
            try:
                doc = project.add_document("Software", "SHLRSCM", "SCM")
                scm = project.sections(doc)[0]["id"]
                iom = project.add_section(doc, "IOM", "SHLRIOM")
                requirement = project.save_requirement(doc, None, "Moved", "", iom, "Usability")
                state = SimpleNamespace(project=project, document=doc, reqs=Mock(), refresh_choices=Mock(),
                                        section_filter=Mock(), type_filter=Mock(), fill_editor=Mock(),
                                        active_filters=("SCM", "Functional"),
                                        section_filters={"SCM": scm}, type_filters={"Functional": "Functional"})
                state.reqs.get_children.return_value = ()
                App.refresh_requirements(state, requirement)
                self.assertEqual(state.active_filters, ("All sections", "All types"))
                self.assertEqual(state.fill_editor.call_args.args[0]["id"], requirement)
                state.reqs.selection_set.assert_called_once_with(requirement)
            finally:
                project.close()

    def test_save_during_navigation_keeps_the_requested_selection(self):
        from app import App
        tree = Mock()
        tree.selection.return_value = ("SYS2",)
        state = SimpleNamespace(reqs=tree, requirement="SYS1", busy=False, document=1,
                                project=Mock(), fill_editor=Mock())
        state.project.requirements.return_value = [{"id": "SYS1"}, {"id": "SYS2"}]

        def save_current():
            tree.selection.return_value = ("SYS1",)
            return True

        state.confirm_edits = save_current
        App.select_requirement(state)
        state.fill_editor.assert_called_once_with({"id": "SYS2"})
        tree.selection_set.assert_called_with("SYS2")

    def test_cancel_navigation_keeps_current_requirement(self):
        from app import App
        state = SimpleNamespace(reqs=Mock(), requirement="SYS1", busy=False,
                                confirm_edits=lambda: False, fill_editor=Mock())
        state.reqs.selection.return_value = ("SYS2",)
        App.select_requirement(state)
        state.reqs.selection_set.assert_called_once_with("SYS1")
        state.fill_editor.assert_not_called()

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
                app.reqs.selection_set("EBBSYS1")
                app.update()
                self.assertEqual(app.requirement, "EBBSYS1")
                self.assertEqual(app.project.requirements(doc)[1]["title"], "Report updated sensor")
                iom = app.project.add_section(doc, "IOM", "SHLRIOM")
                app.refresh_requirements("EBBSYS1")
                app.section_text.set("IOM [SHLRIOM]")
                app.type_text.set("Usability")
                app.save()
                self.assertEqual(app.requirement, "EBBSYS1")
                app.section_filter.set("IOM [SHLRIOM]")
                app.type_filter.set("Type: Usability")
                app.change_filters()
                app.new_requirement()
                app.title_text.set("New IOM requirement")
                app.save()
                self.assertEqual(app.requirement, "SHLRIOM1")
                self.assertEqual(len(app.project.requirements(doc, iom, "Usability")), 2)
                app.project.close()
                app.project = Project(path)
                self.assertEqual(len(app.project.requirements()), 3)
                self.assertEqual(callback_errors, [])
            finally:
                app.project.close()
                app.destroy()


if __name__ == "__main__":
    unittest.main()
