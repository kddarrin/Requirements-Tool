"""Run with python app.py. The GUI delegates all persistent data to storage.py."""
from pathlib import Path
from queue import Empty, Queue
import sqlite3
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from export import export_html, export_pdf
from storage import Project, scan_code
from views import RequirementsView, TraceMatrixView, table


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Requirements Trace")
        self.geometry("1320x800")
        self.minsize(980, 620)
        self.project = self.document = self.requirement = None
        self.baseline, self.busy = ("", "", None, ""), False
        self.section_choices = {}
        self.project_controls, self.document_controls, self.requirement_controls = [], [], []
        ttk.Style(self).configure("Heading.TLabel", font=("TkDefaultFont", 15, "bold"))
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Requirements Trace", style="Heading.TLabel").pack(side="left", padx=(0, 18))
        self.switch_controls = [self.button(toolbar, "New project", lambda: self.open_project(True)),
                                self.button(toolbar, "Open project", self.open_project)]
        self.button(toolbar, "Export HTML", lambda: self.export(False), self.project_controls)
        self.button(toolbar, "Export PDF", lambda: self.export(True), self.project_controls)
        self.button(toolbar, "Scan code", self.scan, self.project_controls)
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10)
        left, middle, right = [ttk.Frame(panes, padding=6) for _ in range(3)]
        for pane, weight in zip((left, middle, right), (1, 2, 3)):
            panes.add(pane, weight=weight)
        ttk.Label(left, text="Documents").pack(anchor="w")
        self.button(left, "New document", self.new_document, self.project_controls, side="top")
        self.docs = table(left, ("Document",), (245,))
        self.docs.bind("<<TreeviewSelect>>", self.safe(self.select_document))
        self.notebook = ttk.Notebook(middle)
        self.notebook.pack(fill="both", expand=True)
        self.requirements_view = RequirementsView(self.notebook, self.select_requirement, self.new_requirement,
                lambda: self.new_document(True), self.delete_section, self.refresh_requirements, self.confirm_edits, self.safe)
        self.notebook.add(self.requirements_view, text="Requirements")
        self.additional_views = []
        self.matrix_view = self.add_view("Trace Matrix", TraceMatrixView)
        self.heading = ttk.Label(right, text="Open or create a project to begin.")
        self.heading.pack(anchor="w", pady=(0, 8))
        fields = ttk.Frame(right)
        fields.pack(fill="x", pady=(0, 8))
        self.section_text, self.type_text = tk.StringVar(), tk.StringVar()
        self.section_entry = ttk.Combobox(fields, textvariable=self.section_text, state="readonly", width=25)
        self.type_entry = ttk.Combobox(fields, textvariable=self.type_text, width=18)
        for column, (label, widget) in enumerate((("Section (prefix for new IDs)", self.section_entry),
                                                  ("Type (optional; editable)", self.type_entry))):
            ttk.Label(fields, text=label).grid(row=0, column=column, sticky="w")
            widget.grid(row=1, column=column, sticky="ew", padx=(0, 5))
            fields.columnconfigure(column, weight=1)
        ttk.Label(right, text="Title").pack(anchor="w")
        self.title_text = tk.StringVar()
        self.title_entry = ttk.Entry(right, textvariable=self.title_text)
        self.title_entry.pack(fill="x", pady=(2, 8))
        ttk.Label(right, text="Description / requirement text").pack(anchor="w")
        self.body = ScrolledText(right, wrap="word", height=8, width=45, undo=True)
        self.body.pack(fill="both", expand=True, pady=4)
        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=5)
        self.button(actions, "Save", self.save, self.document_controls)
        self.button(actions, "Delete", self.delete, self.requirement_controls)
        self.button(actions, "Link…", self.link, self.requirement_controls)
        self.button(actions, "Unlink", self.unlink, self.requirement_controls)
        ttk.Label(right, text="Trace links and code locations").pack(anchor="w")
        self.traces = table(right, ("Kind", "Target / location"), (70, 370))
        self.traces.bind("<Double-1>", self.safe(self.follow_trace))
        ttk.Label(right, text="Double-click a link to navigate, or a code location to copy.").pack(anchor="w")
        self.status = tk.StringVar(value="Create a project, then add a document with your requirement prefix.")
        ttk.Label(self, textvariable=self.status, padding=10, anchor="w").pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.bind("<Control-s>", self.safe(self.save))
        self.sync_controls()

    def safe(self, callback):
        def call(*_):
            try:
                return callback()
            except (ValueError, OSError, sqlite3.Error) as error:
                messagebox.showerror("Requirements Trace", str(error), parent=self)
                return False
        return call

    def button(self, parent, text, callback, group=None, side="left"):
        button = ttk.Button(parent, text=text, command=self.safe(callback))
        button.pack(side=side, padx=(0, 5), pady=3)
        if group is not None:
            group.append(button)
        return button

    def add_view(self, title, view_class):
        view = view_class(self.notebook, self.select_requirement, self.safe)
        self.notebook.add(view, text=title)
        self.additional_views.append(view)
        return view

    def refresh_views(self):
        for view in self.additional_views:
            view.refresh(self.project, self.requirement)

    def sync_controls(self):
        for controls, enabled in ((self.switch_controls, True), (self.project_controls, self.project),
                                  (self.document_controls, self.document),
                                  (self.requirement_controls, self.requirement)):
            for widget in controls:
                widget.configure(state="normal" if enabled and not self.busy else "disabled")
        state = "normal" if self.document and not self.busy else "disabled"
        self.title_entry.configure(state=state)
        self.body.configure(state=state)
        self.type_entry.configure(state=state)
        self.section_entry.configure(state="readonly" if state == "normal" else "disabled")
        self.requirements_view.set_enabled(bool(self.document) and not self.busy)
        for view in self.additional_views:
            view.set_enabled(bool(self.project) and not self.busy)

    def editor_values(self):
        return (self.title_text.get().strip(), self.body.get("1.0", "end-1c").strip(),
                self.section_choices.get(self.section_text.get()), self.type_text.get().strip())

    def confirm_edits(self):
        if self.editor_values() == self.baseline:
            return True
        answer = messagebox.askyesnocancel("Unsaved changes", "Save this requirement before continuing?", parent=self)
        return self.safe(self.save)() if answer else answer is False

    def open_project(self, create=False):
        if self.busy or not self.confirm_edits():
            return
        options = dict(parent=self, filetypes=[("Requirements project", "*.reqtrace"), ("All files", "*")])
        path = (filedialog.asksaveasfilename(title="New project — choose a new filename", defaultextension=".reqtrace",
                                            confirmoverwrite=False, **options) if create
                else filedialog.askopenfilename(title="Open project", **options))
        if not path:
            return
        project = Project(path, create=create)
        if self.project:
            self.project.close()
        self.project, self.document = project, None
        self.requirements_view.active_filters = ("All sections", "All types")
        self.docs.delete(*self.docs.get_children())
        for doc in project.documents():
            self.docs.insert("", "end", iid=str(doc["id"]), values=(doc["name"],))
        self.refresh_requirements()
        if self.docs.get_children():
            self.docs.selection_set(self.docs.get_children()[0])
        self.title(f"{project.path.name} — Requirements Trace")
        self.status.set(str(project.path))

    def dialog(self, title):
        window = tk.Toplevel(self)
        window.title(title)
        window.transient(self)
        window.grab_set()
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        window.bind("<Escape>", lambda _: window.destroy())
        return window, frame

    def new_document(self, section=False):
        if not self.confirm_edits():
            return
        window, frame = self.dialog("New section" if section else "New requirement document")
        ttk.Label(frame, text="Section name" if section else "Document name").pack(anchor="w")
        name = ttk.Entry(frame, width=48)
        name.pack(fill="x", pady=5)
        if not section:
            ttk.Label(frame, text="First section name").pack(anchor="w")
            first_section = ttk.Entry(frame)
            first_section.insert(0, "General")
            first_section.pack(fill="x", pady=5)
        ttk.Label(frame, text="ID prefix — EBBSYS produces EBBSYS1, EBBSYS2, …").pack(anchor="w")
        prefix = ttk.Entry(frame)
        prefix.pack(fill="x", pady=5)

        def create():
            if section:
                doc = self.document
                self.project.add_section(doc, name.get(), prefix.get())
                self.requirements_view.active_filters = (f"{name.get().strip()} [{prefix.get().strip().upper()}]", "All types")
            else:
                doc = self.project.add_document(name.get(), prefix.get(), first_section.get())
                self.docs.insert("", "end", iid=str(doc), values=(name.get().strip(),))
                self.requirements_view.active_filters = ("All sections", "All types")
            self.document = doc
            self.refresh_requirements()
            self.docs.selection_set(str(doc))
            window.destroy()

        self.button(frame, "Create section" if section else "Create document", create)
        name.focus_set()

    def delete_section(self):
        if self.busy or not self.document:
            return
        sections = self.project.sections(self.document)
        if len(sections) < 2:
            raise ValueError("Keep at least one section per document. Create another section first.")
        preferred = self.requirements_view.selected_section() or self.section_choices.get(self.section_text.get())
        if not self.confirm_edits():
            return
        window, frame = self.dialog("Delete section")
        choices = {f"{s['name']} [{s['prefix']}]": s["id"] for s in sections}
        source = tk.StringVar(value=next((name for name, value in choices.items() if value == preferred), next(iter(choices))))
        destination, explanation = tk.StringVar(), tk.StringVar()
        ttk.Label(frame, text="Section to delete").pack(anchor="w")
        source_box = ttk.Combobox(frame, textvariable=source, values=list(choices), state="readonly", width=52)
        source_box.pack(fill="x", pady=5)
        ttk.Label(frame, text="Move its requirements to").pack(anchor="w")
        destination_box = ttk.Combobox(frame, textvariable=destination, state="readonly")
        destination_box.pack(fill="x", pady=5)
        ttk.Label(frame, textvariable=explanation, wraplength=420).pack(anchor="w", pady=8)

        def update_choices():
            others = [name for name in choices if name != source.get()]
            destination_box.configure(values=others)
            if destination.get() not in others:
                destination.set(others[0])
            count = len(self.project.requirements(self.document, choices[source.get()]))
            destination_box.configure(state="readonly" if count else "disabled")
            explanation.set(f"{count} requirements will move. IDs, types, links, and code tags are preserved."
                            if count else "This section is empty and can be removed.")

        def remove():
            section = choices[source.get()]
            count = len(self.project.requirements(self.document, section))
            target = choices[destination.get()] if count else None
            message = (f"Delete {source.get()} and move {count} requirements to {destination.get()}?"
                       if count else f"Delete empty section {source.get()}?")
            if not messagebox.askyesno("Delete section", message, parent=window):
                return
            moved = self.project.delete_section(self.document, section, target)
            self.requirements_view.forget_section(section)
            self.refresh_requirements(self.requirement)
            self.status.set(f"Deleted {source.get()}. {moved} requirements moved.")
            window.destroy()

        source_box.bind("<<ComboboxSelected>>", self.safe(update_choices))
        update_choices()
        self.button(frame, "Delete section", remove)
        self.button(frame, "Cancel", window.destroy)

    def select_document(self):
        selected = self.docs.selection()
        if not selected or int(selected[0]) == self.document:
            return
        if self.busy or not self.confirm_edits():
            if self.document:
                self.docs.selection_set(str(self.document))
            else:
                self.docs.selection_remove(*selected)
            return
        self.document = int(selected[0])
        self.requirements_view.active_filters = ("All sections", "All types")
        self.refresh_requirements()

    def refresh_choices(self):
        self.section_choices = self.requirements_view.section_choices
        self.section_entry.configure(values=list(self.section_choices))
        suggestions = sorted(set(self.requirements_view.types) | {"Functional", "Performance", "Usability", "Interface"}, key=str.casefold)
        self.type_entry.configure(values=suggestions)

    def refresh_requirements(self, select=None):
        self.requirements_view.refresh(self.project, self.document, select)
        self.refresh_choices()
        row = next((r for r in self.requirements_view.visible if r["id"] == self.requirements_view.selected), None)
        self.fill_editor(row)
        self.refresh_views()

    def fill_editor(self, row):
        self.requirement = row["id"] if row else None
        self.title_text.set(row["title"] if row else "")
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", row["description"] if row else "")
        self.body.edit_reset()
        default_section, default_type = self.requirements_view.defaults()
        section = row["section"] if row else default_section
        if section is None:
            section = self.section_choices.get(self.section_text.get())
        label = next((label for label, value in self.section_choices.items() if value == section),
                     next(iter(self.section_choices), ""))
        self.section_text.set(label)
        self.type_text.set(row["type"] if row else default_type or "")
        self.baseline = self.editor_values()
        label = "New requirement — ID assigned when saved" if self.document else "Select or create a document."
        self.heading.configure(text=self.requirement or label)
        self.traces.delete(*self.traces.get_children())
        if row:
            trace = next(r for r in self.project.matrix() if r["id"] == self.requirement)
            for key, kind in (("parents", "Parent"), ("children", "Child"), ("code", "Code")):
                for target in trace[key]:
                    self.traces.insert("", "end", values=(kind, target))
        self.sync_controls()
        for view in self.additional_views:
            view.highlight(self.requirement)

    def select_requirement(self, requirement):
        if self.busy:
            return False
        if requirement == self.requirement:
            return True
        if not self.confirm_edits():
            return False
        row = next((r for r in self.project.requirements() if r["id"] == requirement), None)
        if row is None:
            return False
        if self.document != row["document"]:
            self.requirements_view.active_filters = ("All sections", "All types")
        self.document = row["document"]
        self.docs.selection_set(str(self.document))
        self.refresh_requirements(requirement)
        return True

    def new_requirement(self):
        if self.confirm_edits():
            self.requirements_view.select(None)
            self.fill_editor(None)
            self.title_entry.focus_set()

    def save(self):
        if not self.document or self.busy:
            return False
        requirement = self.project.save_requirement(self.document, self.requirement, *self.editor_values())
        self.refresh_requirements(requirement)
        self.status.set(f"Saved {requirement}. IDs remain fixed when edited or deleted.")
        return True

    def delete(self):
        if messagebox.askyesno("Delete requirement", f"Delete {self.requirement} and its links? Its ID stays reserved.", parent=self):
            self.project.delete_requirement(self.requirement)
            self.refresh_requirements()

    def link(self):
        if not self.confirm_edits():
            return
        window, frame = self.dialog(f"Link {self.requirement}")
        ttk.Label(frame, text="Link the requirement chosen below as a:").pack(anchor="w")
        relation = ttk.Combobox(frame, values=(f"Child of {self.requirement}", f"Parent of {self.requirement}"),
                                state="readonly", width=45)
        relation.current(0)
        relation.pack(fill="x", pady=6)
        choices = table(frame, ("ID", "Document", "Section", "Title"), (130, 180, 140, 260))
        for row in self.project.requirements():
            if row["document"] != self.document:
                choices.insert("", "end", iid=row["id"], values=(row["id"], row["document_name"], row["section_name"], row["title"]))

        def connect():
            if not choices.selection():
                raise ValueError("Choose a requirement from another document. Create one first if the list is empty.")
            other = choices.selection()[0]
            parent, child = (self.requirement, other) if relation.current() == 0 else (other, self.requirement)
            self.project.link(parent, child)
            self.refresh_requirements(self.requirement)
            window.destroy()

        self.button(frame, "Create link", connect)

    def unlink(self):
        selected = self.traces.selection()
        if not selected:
            raise ValueError("Select a parent or child trace link to remove.")
        kind, target = self.traces.item(selected[0], "values")
        if kind == "Code":
            raise ValueError("Code locations come from scans. Edit the source tag and scan again.")
        parent, child = (target, self.requirement) if kind == "Parent" else (self.requirement, target)
        self.project.unlink(parent, child)
        # Refresh only the traces so unsaved editor changes survive unlinking.
        self.traces.delete(selected[0])
        self.refresh_views()

    def follow_trace(self):
        selected = self.traces.selection()
        if not selected or self.busy:
            return
        kind, target = self.traces.item(selected[0], "values")
        if kind == "Code":
            self.clipboard_clear()
            self.clipboard_append(target)
            self.status.set(f"Copied {target}")
        else:
            self.select_requirement(target)

    def scan(self):
        if not self.confirm_edits():
            return
        folder = filedialog.askdirectory(title="Scan source folder (replaces the previous scan)", parent=self)
        if not folder:
            return
        known, result = {r["id"] for r in self.project.requirements()}, Queue()
        self.busy = True
        self.sync_controls()
        self.status.set(f"Scanning {folder} …")

        def worker():
            try:
                result.put(scan_code(folder, known))
            except Exception as error:
                result.put(error)

        def finish():
            try:
                scanned = result.get_nowait()
            except Empty:
                self.after(100, self.safe(finish))
                return
            self.busy = False
            self.sync_controls()
            if isinstance(scanned, Exception):
                self.status.set("Scan failed; the previous scan is unchanged.")
                raise ValueError(str(scanned))
            refs, unknown, skipped = scanned
            self.project.replace_code_refs(refs)
            self.refresh_requirements(self.requirement)
            summary = f"{len(refs)} code locations; {len(unknown)} unknown tags; {len(skipped)} skipped/read errors."
            self.status.set(summary)
            window, frame = self.dialog("Scan results")
            text = ScrolledText(frame, width=95, height=22, wrap="word")
            text.pack(fill="both", expand=True)
            text.insert("end", summary + "\n\nUnknown tags:\n" + "\n".join(unknown or ["None"]) +
                        "\n\nSkipped/read errors:\n" + "\n".join(skipped or ["None"]))
            text.configure(state="disabled")
            self.button(frame, "Close", window.destroy)

        Thread(target=worker, daemon=True).start()
        self.after(100, self.safe(finish))

    def export(self, pdf):
        if not self.confirm_edits():
            return
        extension = ".pdf" if pdf else ".html"
        path = filedialog.asksaveasfilename(parent=self, title="Export trace matrix", defaultextension=extension,
                                          initialfile=self.project.path.stem + "-matrix" + extension,
                                          filetypes=[("Trace matrix", "*" + extension)])
        if path:
            if Path(path).resolve() == self.project.path:
                raise ValueError("Choose a filename different from the project file.")
            (export_pdf if pdf else export_html)(path, self.project.path.stem, self.project.matrix())
            self.status.set(f"Exported {path}")

    def quit_app(self):
        if self.confirm_edits():
            if self.project:
                self.project.close()
            self.destroy()


if __name__ == "__main__":
    App().mainloop()
