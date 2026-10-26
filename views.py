"""Reusable Tkinter views. Data comes from Project; selection goes to the editor."""
from itertools import groupby
import re
import tkinter as tk
from tkinter import font, ttk


def table(parent, headings, widths, grouped=False):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True, pady=6)
    tree = ttk.Treeview(frame, columns=headings, show="tree headings" if grouped else "headings",
                        selectmode="browse", height=7)
    for heading, width in zip(headings, widths):
        tree.heading(heading, text=heading)
        tree.column(heading, width=width, minwidth=65)
    vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return tree


def sort_requirements(rows, column="ID", descending=False):
    def natural_id(row):
        return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", row["id"]))

    key = natural_id if column == "ID" else lambda row: (row[column.lower()].casefold(), natural_id(row))
    return sorted(rows, key=key, reverse=descending)


class RequirementsView(ttk.Frame):
    def __init__(self, parent, on_select, on_add, on_section, on_delete_section, on_filter, confirm_edits, guard):
        super().__init__(parent, padding=6)
        self.on_select, self.on_filter, self.confirm_edits = on_select, on_filter, confirm_edits
        self.active_filters = ("All sections", "All types")
        self.section_choices, self.section_filters, self.type_filters = {}, {}, {}
        self.rows, self.sections, self.types, self.visible = [], [], [], []
        self.selected, self.project_path, self.document = None, None, None
        self.sort_column, self.descending, self.closed, self.enabled = "ID", False, set(), False
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        self.buttons = []
        for text, callback in (("Add requirement", on_add), ("New section", on_section), ("Delete section", on_delete_section)):
            button = ttk.Button(actions, text=text, command=guard(callback))
            button.pack(side="left", padx=(0, 5))
            self.buttons.append(button)
        self.section_filter, self.type_filter = tk.StringVar(), tk.StringVar()
        self.section_box = ttk.Combobox(self, textvariable=self.section_filter, state="readonly")
        self.type_box = ttk.Combobox(self, textvariable=self.type_filter, state="readonly")
        for label, box in (("Section filter", self.section_box), ("Type filter", self.type_box)):
            ttk.Label(self, text=label).pack(anchor="w", pady=(5, 0))
            box.pack(fill="x")
            box.bind("<<ComboboxSelected>>", guard(self.change_filters))
        self.tree = table(self, ("Title", "Type"), (190, 90), grouped=True)
        self.tree.column("#0", width=190, minwidth=100)
        self.tree.tag_configure("section", background="#e4eef5", font=("TkDefaultFont", 9, "bold"))
        for column, heading in (("ID", "#0"), ("Title", "Title"), ("Type", "Type")):
            self.tree.heading(heading, command=guard(lambda column=column: self.sort(column)))
        self.tree.bind("<<TreeviewSelect>>", guard(self.choose))
        self.count = ttk.Label(self)
        self.count.pack(anchor="w")
        self.refresh(None, None)

    def set_enabled(self, enabled):
        self.enabled = enabled
        for button in self.buttons:
            button.configure(state="normal" if enabled else "disabled")
        for box in (self.section_box, self.type_box):
            box.configure(state="readonly" if enabled else "disabled")

    def defaults(self):
        return self.section_filters.get(self.active_filters[0]), self.type_filters.get(self.active_filters[1])

    def selected_section(self):
        selected = self.tree.selection()
        if selected and selected[0].startswith("section:"):
            return int(selected[0].split(":")[1])
        return next((r["section"] for r in self.rows if selected and r["id"] == selected[0]), self.defaults()[0])

    def forget_section(self, section):
        group = f"section:{section}"
        self.closed.discard(group)
        if self.tree.exists(group):
            self.tree.delete(group)

    def show_filters(self):
        for box, variable, choices, label in zip((self.section_box, self.type_box),
                (self.section_filter, self.type_filter), (self.section_filters, self.type_filters), self.active_filters):
            box.configure(values=list(choices))
            variable.set(label)

    def change_filters(self):
        requested = (self.section_filter.get(), self.type_filter.get())
        if requested == self.active_filters:
            return
        if not self.enabled or not self.confirm_edits():
            self.show_filters()
            return
        self.active_filters = requested
        self.on_filter()

    def refresh(self, project, document, select=None):
        path = project.path if project else None
        if path != self.project_path:
            self.tree.delete(*self.tree.get_children())
            self.closed.clear()
            self.active_filters = ("All sections", "All types")
        self.project_path, self.document = path, document
        self.sections = project.sections(document) if project and document else []
        self.rows = project.requirements(document) if project and document else []
        self.types = sorted({r["type"] for r in self.rows if r["type"]}, key=str.casefold)
        self.section_choices = {f"{s['name']} [{s['prefix']}]": s["id"] for s in self.sections}
        self.section_filters = {"All sections": None, **self.section_choices}
        self.type_filters = {"All types": None, "Unspecified": "", **{f"Type: {t}": t for t in self.types}}
        self.active_filters = tuple(label if label in choices else next(iter(choices))
                                    for label, choices in zip(self.active_filters, (self.section_filters, self.type_filters)))
        section, kind = self.defaults()
        self.visible = [r for r in self.rows if (section is None or r["section"] == section)
                        and (kind is None or r["type"] == kind)]
        if select and any(r["id"] == select for r in self.rows) and not any(r["id"] == select for r in self.visible):
            self.active_filters = ("All sections", "All types")
            self.visible = self.rows
        self.show_filters()
        self.selected = next((r["id"] for r in self.visible if r["id"] == select),
                             self.visible[0]["id"] if self.visible else None)
        self.render()
        self.select(self.selected, reveal=True)

    def render(self):
        for group in self.tree.get_children():
            if self.tree.item(group, "open"):
                self.closed.discard(group)
            else:
                self.closed.add(group)
        self.tree.delete(*self.tree.get_children())
        section_filter, _ = self.defaults()
        for section in self.sections:
            if section_filter is not None and section["id"] != section_filter:
                continue
            rows = [r for r in self.visible if r["section"] == section["id"]]
            group = f"section:{section['id']}"
            self.tree.insert("", "end", iid=group, text=f"{section['name']} [{section['prefix']}] — {len(rows)}",
                             open=group not in self.closed, tags=("section",))
            for row in sort_requirements(rows, self.sort_column, self.descending):
                self.tree.insert(group, "end", iid=row["id"], text=row["id"], values=(row["title"], row["type"]))
        for column, heading in (("ID", "#0"), ("Title", "Title"), ("Type", "Type")):
            label = "Section / ID" if column == "ID" else column
            if column == self.sort_column:
                label += " ▼" if self.descending else " ▲"
            self.tree.heading(heading, text=label)
        self.count.configure(text=f"{len(self.visible)} of {len(self.rows)} requirements shown")
        self.select(self.selected)

    def sort(self, column):
        self.descending = not self.descending if column == self.sort_column else False
        self.sort_column = column
        self.render()

    def select(self, requirement, reveal=False):
        self.selected = requirement
        if requirement and self.tree.exists(requirement):
            self.tree.selection_set(requirement)
            if reveal:
                self.tree.see(requirement)
        else:
            self.tree.selection_remove(*self.tree.selection())

    def choose(self):
        selected = self.tree.selection()
        if not selected or selected[0].startswith("section:") or selected[0] == self.selected:
            return
        previous = self.selected
        self.select(selected[0] if self.on_select(selected[0]) else previous, reveal=True)


def trace_columns(rows, left, right):
    """Keep every requirement in the chosen documents and only direct cross-document links."""
    columns = [[r for r in rows if r["document"] == document] for document in (left, right)]
    left_ids, right_ids = ({r["id"] for r in column} for column in columns)
    links = [(r["id"], child) for r in rows for child in r["children"]
             if (r["id"] in left_ids and child in right_ids) or (r["id"] in right_ids and child in left_ids)]
    return columns[0], columns[1], links


class TraceMatrixView(ttk.Frame):
    def __init__(self, parent, on_select, guard):
        super().__init__(parent, padding=6)
        self.on_select, self.guard = on_select, guard
        self.project_path, self.rows, self.documents = None, [], {}
        self.left = self.right = self.selected = None
        self.nodes, self.lines, self.enabled = {}, [], False
        selectors = ttk.Frame(self)
        selectors.pack(fill="x")
        self.left_name, self.right_name = tk.StringVar(), tk.StringVar()
        self.boxes = []
        for column, (label, variable) in enumerate((("Left document", self.left_name), ("Right document", self.right_name))):
            ttk.Label(selectors, text=label).grid(row=0, column=column, sticky="w")
            box = ttk.Combobox(selectors, textvariable=variable, state="readonly", width=18)
            box.grid(row=1, column=column, sticky="ew", padx=(0, 5))
            box.bind("<<ComboboxSelected>>", guard(lambda column=column: self.change_documents(column)))
            selectors.columnconfigure(column, weight=1)
            self.boxes.append(box)
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, pady=6)
        self.canvas = tk.Canvas(frame, width=500, height=400, background="#f7fafc", highlightthickness=0)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-3 if event.delta > 0 else 3, "units"))
        self.canvas.bind("<Button-4>", lambda _: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _: self.canvas.yview_scroll(3, "units"))
        self.text_font = font.nametofont("TkDefaultFont")
        self.summary = ttk.Label(self, wraplength=500)
        self.summary.pack(anchor="w")
        ttk.Label(self, text="Arrows: parent → child. Click a requirement to highlight its links.", wraplength=500).pack(anchor="w")

    def set_enabled(self, enabled):
        self.enabled = enabled
        for box in self.boxes:
            box.configure(state="readonly" if enabled and len(self.documents) >= 2 else "disabled")

    def refresh(self, project, selected=None):
        path = project.path if project else None
        documents = {d["name"]: d["id"] for d in project.documents()} if project else {}
        rows = project.matrix() if project else []
        changed = path != self.project_path or documents != self.documents or rows != self.rows
        if path != self.project_path:
            self.left = self.right = None
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        self.project_path, self.documents, self.rows, self.selected = path, documents, rows, selected
        ids = list(documents.values())
        if self.left not in ids:
            self.left = ids[0] if ids else None
        if self.right not in ids or self.right == self.left:
            self.right = next((i for i in ids if i != self.left), None)
        self.show_documents()
        self.set_enabled(self.enabled)
        if changed:
            self.draw()
        else:
            self.highlight(selected)

    def show_documents(self):
        for box, variable, identifier in zip(self.boxes, (self.left_name, self.right_name), (self.left, self.right)):
            box.configure(values=list(self.documents))
            variable.set(next((name for name, value in self.documents.items() if value == identifier), ""))

    def change_documents(self, side):
        self.left = self.documents.get(self.left_name.get())
        self.right = self.documents.get(self.right_name.get())
        if self.left == self.right:
            other = next((i for i in self.documents.values() if i != self.left), None)
            if side == 0:
                self.right = other
            else:
                self.left = other
        self.show_documents()
        self.draw()

    def shorten(self, text, width):
        short = text.replace("\n", " ")[:120]
        while short and self.text_font.measure(short + "…") > width:
            short = short[:-1]
        return short + ("…" if short != text else "")

    def draw(self):
        xscroll, yscroll = self.canvas.xview()[0], self.canvas.yview()[0]
        self.canvas.delete("all")
        self.nodes, self.lines = {}, []
        width, height = max(self.canvas.winfo_width(), 480), 100
        if self.left is None or self.right is None:
            self.canvas.create_text(20, 30, anchor="nw", text="Create two documents to compare their requirements.")
            self.summary.configure(text="Choose two different documents.")
        else:
            left, right, links = trace_columns(self.rows, self.left, self.right)
            positions, node_width = {}, (width - 32 - min(160, width * .24)) / 2
            for side, rows in enumerate((left, right)):
                x, y = (16 if side == 0 else width - 16 - node_width), 16
                for _, items in groupby(rows, key=lambda row: row["section"]):
                    items = list(items)
                    label = f"{items[0]['section_name']} [{items[0]['section_prefix']}] — {len(items)}"
                    self.canvas.create_text(x, y, anchor="nw", text=self.shorten(label, node_width), fill="#294d68")
                    y += 28
                    for row in items:
                        tag = "node:" + row["id"]
                        rect = self.canvas.create_rectangle(x, y, x + node_width, y + 50, fill="white", outline="#bdcbd5", tags=tag)
                        self.canvas.create_text(x + 8, y + 8, anchor="nw", text=self.shorten(row["id"], node_width - 16), tags=tag)
                        self.canvas.create_text(x + 8, y + 28, anchor="nw", text=self.shorten(row["title"], node_width - 16), tags=tag)
                        self.canvas.tag_bind(tag, "<Button-1>", self.guard(lambda identifier=row["id"]: self.choose(identifier)))
                        self.nodes[row["id"]] = rect
                        positions[row["id"]] = (x + node_width if side == 0 else x, y + 25)
                        y += 62
                if not rows:
                    self.canvas.create_text(x, y, anchor="nw", text="No requirements in this document.")
                height = max(height, y + 16)
            for parent, child in links:
                item = self.canvas.create_line(*positions[parent], *positions[child], arrow="last", fill="#b8c7d2", tags="trace")
                self.lines.append((parent, child, item))
            self.canvas.tag_lower("trace")
            self.summary.configure(text=f"{len(left)} left requirements · {len(right)} right requirements · {len(links)} direct links")
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.canvas.xview_moveto(xscroll)
        self.canvas.yview_moveto(yscroll)
        self.highlight(self.selected)

    def highlight(self, selected):
        self.selected = selected
        connected = set()
        for parent, child, item in self.lines:
            active = selected in (parent, child)
            if active:
                connected.update((parent, child))
            self.canvas.itemconfigure(item, fill="#1769aa" if active else "#b8c7d2", width=2.5 if active else 1)
            if active:
                self.canvas.tag_raise(item)
        self.canvas.tag_lower("trace")
        for requirement, rect in self.nodes.items():
            fill = "#fff0b3" if requirement == selected else "#e1f0fc" if requirement in connected else "white"
            self.canvas.itemconfigure(rect, fill=fill, outline="#1769aa" if requirement in connected else "#bdcbd5")

    def choose(self, requirement):
        if self.enabled and self.on_select(requirement):
            self.highlight(requirement)
