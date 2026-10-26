"""View behavior checks that run without a desktop display."""
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock

from storage import Project
from views import RequirementsView, TraceMatrixView, sort_requirements, trace_columns


class Tree:
    """Small Treeview stand-in for hierarchy, collapse, and selection checks."""
    def __init__(self):
        self.items, self.selected = {}, ()

    def insert(self, parent, position, iid, **options):
        self.items[iid] = dict(options, parent=parent)

    def get_children(self, parent=""):
        return tuple(key for key, item in self.items.items() if item["parent"] == parent)

    def delete(self, *items):
        for key in items:
            self.delete(*self.get_children(key))
            del self.items[key]
        self.selected = tuple(key for key in self.selected if key in self.items)

    def item(self, key, option=None, **options):
        self.items[key].update(options)
        return self.items[key].get(option) if option else self.items[key]

    def exists(self, key):
        return key in self.items

    def selection(self):
        return self.selected

    def selection_set(self, key):
        self.selected = (key,)

    def selection_remove(self, *keys):
        self.selected = tuple(key for key in self.selected if key not in keys)

    def see(self, key):
        parent = self.items[key]["parent"]
        if parent:
            self.items[parent]["open"] = True

    def heading(self, *args, **kwargs):
        pass


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.project = Project(Path(self.temp.name) / "views.reqtrace", create=True)
        self.doc = self.project.add_document("Software", "SHLRSCM", "SCM")
        self.scm = self.project.sections(self.doc)[0]["id"]
        self.iom = self.project.add_section(self.doc, "IOM", "SHLRIOM")
        self.first = self.project.save_requirement(self.doc, None, "Zulu", "", self.scm, "Functional")
        self.second = self.project.save_requirement(self.doc, None, "Alpha", "", self.scm, "Usability")
        self.third = self.project.save_requirement(self.doc, None, "Beta", "", self.iom, "Functional")
        self.system = self.project.add_document("System", "SYS")
        self.parent = self.project.save_requirement(self.system, None, "Parent", "")

    def tearDown(self):
        self.project.close()
        self.temp.cleanup()

    def requirement_view(self):
        view = SimpleNamespace(tree=Tree(), project_path=None, closed=set(), count=Mock(), enabled=True,
                active_filters=("All sections", "All types"), sort_column="ID", descending=False,
                section_filter=Mock(), type_filter=Mock(), section_box=Mock(), type_box=Mock(), on_select=Mock())
        for name in ("defaults", "show_filters", "render", "select", "refresh", "sort", "choose", "selected_section", "forget_section"):
            setattr(view, name, MethodType(getattr(RequirementsView, name), view))
        view.refresh(self.project, self.doc, self.first)
        return view

    def test_headers_sort_within_sections_and_keep_collapsed_selection(self):
        view = self.requirement_view()
        group = f"section:{self.scm}"
        other = f"section:{self.iom}"
        self.assertEqual(view.tree.get_children(), (group, other))
        self.assertIn("SCM [SHLRSCM] — 2", view.tree.item(group, "text"))
        view.tree.item(group, open=False)
        view.sort("Title")
        self.assertEqual(view.tree.get_children(group), (self.second, self.first))
        self.assertEqual(view.tree.get_children(other), (self.third,))
        self.assertFalse(view.tree.item(group, "open"))
        self.assertEqual(view.selected, self.first)
        view.on_select.assert_not_called()
        view.sort("Title")
        self.assertEqual(view.tree.get_children(group), (self.first, self.second))

    def test_id_sort_uses_numeric_order(self):
        rows = [{"id": identifier} for identifier in ("SHLRSCM10", "SHLRSCM2", "SHLRSCM1")]
        self.assertEqual([r["id"] for r in sort_requirements(rows)], ["SHLRSCM1", "SHLRSCM2", "SHLRSCM10"])
        self.assertEqual(sort_requirements(rows, descending=True), list(reversed(sort_requirements(rows))))

    def test_section_deletion_uses_header_selection_and_forgets_old_group(self):
        view = self.requirement_view()
        group = f"section:{self.iom}"
        view.tree.selection_set(group)
        self.assertEqual(view.selected_section(), self.iom)
        view.tree.item(group, open=False)
        view.closed.add(group)
        self.project.delete_section(self.doc, self.iom, self.scm)
        view.forget_section(self.iom)
        view.refresh(self.project, self.doc, self.third)
        self.assertFalse(view.tree.exists(group))
        self.assertNotIn(group, view.closed)
        self.assertEqual(view.selected, self.third)
        self.assertEqual(view.tree.item(self.third, "parent"), f"section:{self.scm}")

    def test_headers_do_not_navigate_and_cancel_restores_selection(self):
        view = self.requirement_view()
        view.tree.selection_set(f"section:{self.scm}")
        view.choose()
        view.on_select.assert_not_called()
        view.on_select.return_value = False
        view.tree.selection_set(self.second)
        view.choose()
        self.assertEqual(view.tree.selection(), (self.first,))
        self.assertEqual(view.selected, self.first)

    def test_untyped_filter_keeps_empty_section_headers(self):
        view = self.requirement_view()
        untyped = self.project.save_requirement(self.doc, None, "Untyped", "", self.scm, "")
        view.active_filters = ("All sections", "Unspecified")
        view.refresh(self.project, self.doc)
        self.assertEqual([r["id"] for r in view.visible], [untyped])
        self.assertEqual(view.tree.get_children(f"section:{self.iom}"), ())
        self.assertIn("— 0", view.tree.item(f"section:{self.iom}", "text"))

    def test_new_project_does_not_inherit_collapsed_section_ids(self):
        view = self.requirement_view()
        view.tree.item(f"section:{self.scm}", open=False)
        other = Project(Path(self.temp.name) / "other.reqtrace", create=True)
        try:
            doc = other.add_document("New", "NEW")
            other.save_requirement(doc, None, "New requirement", "")
            view.refresh(other, doc)
            self.assertTrue(view.tree.item("section:1", "open"))
        finally:
            other.close()

    def comparison_rows(self):
        second_system = self.project.save_requirement(self.system, None, "Other system requirement", "")
        self.project.link(self.parent, self.first)
        self.project.link(self.second, second_system)
        outside = self.project.add_document("Other document", "OTHER")
        outside_req = self.project.save_requirement(outside, None, "Outside", "")
        self.project.link(outside_req, self.third)
        return self.project.matrix(), second_system

    def test_comparison_includes_unlinked_rows_and_both_link_directions(self):
        rows, second_system = self.comparison_rows()
        left, right, links = trace_columns(rows, self.doc, self.system)
        self.assertEqual({r["id"] for r in left}, {self.first, self.second, self.third})
        self.assertEqual({r["id"] for r in right}, {self.parent, second_system})
        self.assertEqual(set(links), {(self.parent, self.first), (self.second, second_system)})
        reversed_left, reversed_right, reversed_links = trace_columns(rows, self.system, self.doc)
        self.assertEqual((reversed_left, reversed_right, reversed_links), (right, left, links))

    def test_drawing_keeps_arrow_direction_and_highlights_all_neighbors(self):
        rows, second_system = self.comparison_rows()
        canvas = Mock()
        canvas.xview.return_value = canvas.yview.return_value = (0, 1)
        canvas.winfo_width.return_value = 660
        identifiers = count(1)
        canvas.create_rectangle.side_effect = lambda *args, **kwargs: next(identifiers)
        canvas.create_line.side_effect = lambda *args, **kwargs: next(identifiers)
        view = SimpleNamespace(canvas=canvas, left=self.doc, right=self.system, rows=rows, summary=Mock(),
                               selected=self.parent, guard=lambda fn: fn, shorten=lambda text, width: text, choose=Mock())
        view.highlight = MethodType(TraceMatrixView.highlight, view)
        TraceMatrixView.draw(view)
        self.assertEqual(set(view.nodes), {self.first, self.second, self.third, self.parent, second_system})
        self.assertEqual(len(view.lines), 2)
        for (parent, child, _), call in zip(view.lines, canvas.create_line.call_args_list):
            self.assertEqual(call.kwargs["arrow"], "last")
            if parent == self.parent:
                self.assertGreater(call.args[0], call.args[2])
            else:
                self.assertLess(call.args[0], call.args[2])
        styles = {call.args[0]: call.kwargs for call in canvas.itemconfigure.call_args_list}
        self.assertEqual(styles[view.nodes[self.parent]]["fill"], "#fff0b3")
        self.assertEqual(styles[view.nodes[self.first]]["fill"], "#e1f0fc")
        self.assertEqual(styles[view.nodes[self.third]]["fill"], "white")

    def test_matrix_navigation_cancel_keeps_previous_highlight(self):
        view = SimpleNamespace(enabled=True, on_select=Mock(return_value=False), highlight=Mock(), selected=self.parent)
        TraceMatrixView.choose(view, self.first)
        self.assertEqual(view.selected, self.parent)
        view.highlight.assert_not_called()

    def test_comparison_selectors_keep_documents_distinct(self):
        view = SimpleNamespace(documents={"Software": self.doc, "System": self.system},
                               left_name=Mock(), right_name=Mock(), show_documents=Mock(), draw=Mock())
        view.left_name.get.return_value = view.right_name.get.return_value = "System"
        TraceMatrixView.change_documents(view, 0)
        self.assertEqual((view.left, view.right), (self.system, self.doc))
        TraceMatrixView.change_documents(view, 1)
        self.assertEqual((view.left, view.right), (self.doc, self.system))

    def test_matrix_refresh_tracks_link_changes_and_preserves_document_pair(self):
        view = SimpleNamespace(project_path=None, left=None, right=None, documents={}, rows=[], selected=None,
                               enabled=True, canvas=Mock(), show_documents=Mock(), set_enabled=Mock(), draw=Mock(), highlight=Mock())
        TraceMatrixView.refresh(view, self.project, self.first)
        pair = (view.left, view.right)
        view.draw.reset_mock()
        TraceMatrixView.refresh(view, self.project, self.parent)
        view.draw.assert_not_called()
        view.highlight.assert_called_with(self.parent)
        self.project.link(self.parent, self.first)
        TraceMatrixView.refresh(view, self.project, self.first)
        view.draw.assert_called_once_with()
        self.assertEqual((view.left, view.right), pair)


if __name__ == "__main__":
    unittest.main()
