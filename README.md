# Requirements Trace

A small Python desktop tool for requirements, document-to-document trace links,
and source-code tags. Four application files; no web server or framework.

## Run

Python 3.10+ with Tkinter is required. Start with:

```sh
python3 app.py
```

Project editing, scanning, and HTML export use only Python's standard library.
For direct PDF export, install the one dependency in a virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

On Windows use `python` instead of `python3` and activate with
`.venv\Scripts\activate`. If importing Tkinter fails, install the Tkinter package
for your Python installation (commonly `python3-tk` on Debian/Ubuntu).

## First project

1. Click **New project** and choose a new `.reqtrace` filename.
2. Click **New document**. Give it a name such as `System Requirements`, first
   section `General`, and prefix `SYS`. Create `Software High-Level Requirements`
   with first section `SCM` and prefix `SHLRSCM`.
3. Select the software document and click **New section** to add `IOM` with prefix
   `SHLRIOM`, then `DDM` with prefix `SHLRDDM`. Each section numbers independently.
4. Click **Add requirement**, choose its **Section**, and enter a title and
   description. Optionally choose or type a **Type**, such as `Functional`,
   `Usability`, or your own label. Click **Save** (`Ctrl+S`); SCM's first requirement
   becomes `SHLRSCM1`. Clicking an existing row opens it for editing.
5. Select a requirement, click **Link…**, choose a requirement from another
   document, and specify whether that chosen requirement is a parent or child.
   Links appear from both ends. Double-click a parent or child to navigate to it;
   select a link and click **Unlink** to remove it.
6. Add tags to code comments, then click **Scan code** and choose the source root:

   ```python
   # REQ: SHLRSCM1
   # REQ: SHLRIOM1
   def read_sensor():
       pass
   ```

7. Use **Export HTML** or **Export PDF** to export the entire project's matrix.
   Requirements are grouped under document, section (with prefix), and type
   headings. Rows contain IDs, text, parent/child IDs, code locations, and coverage
   indicators. HTML can also be printed to PDF from your browser.
8. Close and use **Open project** to reopen the `.reqtrace` file later. All
   documents live inside that single file. Close the app before copying a project
   file for a backup.

Use the **Section filter** and **Type filter** above the requirement list to focus
on a module or requirement type. New requirements use those filters as defaults.
Filters affect the list; exports always include the entire project. If an edit
makes a requirement fall outside the filters, the app clears the filters to keep
the saved requirement visible.

To move an existing requirement within its document, change **Section** and
**Save**. Its ID stays fixed, including its original prefix. Changing its type
also preserves its ID, trace links, and code tags.

To remove a section, select its header or one of its requirements in the
**Requirements** tab, then click **Delete section**. The dialog lets you choose
the section explicitly. Empty sections can be deleted directly. For a populated
section, choose another section in the same document to receive all its
requirements, then confirm. IDs, types, links, and code locations stay intact.
The table, filters, and trace diagram refresh after deletion.

Each document keeps at least one section. To replace its only section, create
another first. A removed section's prefix can be used again, while previously
issued requirement IDs remain reserved, including deleted requirement IDs.

## Requirements and Trace Matrix views

The middle pane has two tabs. Both use the same project and requirement editor
on the right. Switching tabs keeps your draft. Selecting a different requirement
from either view prompts about unsaved changes. Drag the pane dividers or use the
scrollbars to give a view more room.

**Requirements** groups rows under collapsible section headers, such as
`SCM [SHLRSCM] — 12`. The count reflects the active filters; empty matching
sections are also shown. Click a section's arrow to collapse or expand it.
Section headers organize the list; requirement rows open the editor.

Click **Section / ID**, **Title**, or **Type** to sort requirements within every
section. Click again to reverse the order. ID sorting uses numeric order
(`SHLRSCM1`, `SHLRSCM2`, `SHLRSCM10`). Sorting keeps the selected requirement,
draft, and collapsed sections. Sort/filter/collapse preferences last for this
session and are not saved into the project.

**Trace Matrix** compares two different documents:

1. Open the tab and choose a **Left document** and **Right document**.
2. Each column lists all requirements from that document, grouped by section,
   including those with no links. Requirement-table filters do not limit this view.
3. Lines show existing direct links between the chosen documents. Arrows point
   from parent to child, including when the parent is in the right column.
4. Click a requirement to highlight its connections and load it into the editor.
   The selected requirement turns yellow and its connected requirements turn
   blue. Full titles and descriptions are available in the editor.
5. Use the shared **Link…**, **Unlink**, **Save**, and **Delete** controls to make
   changes. The views refresh after changes. Trace lines themselves are for viewing.

The comparison needs at least two documents. Selecting the same document on both
sides adjusts the other selector to a different document. Vertical scrolling
moves both columns together; horizontal scrolling supports narrower windows.
The matrix shows links between these two documents; intermediate requirements in
other documents are not expanded into indirect links. HTML/PDF exports remain
the grouped project-wide tabular matrix.

## Rules and limits

- Prefixes are converted to uppercase, start with a letter, and may contain
  letters, digits, underscores, and hyphens. Each section has its own project-wide
  unique prefix. Section names are unique within their document.
- IDs are unique throughout the project, stay fixed, and are never reused after
  deletion. Deleted records reserve their IDs; new IDs can therefore have gaps.
- A duplicate is the same **title and description** in the same document after
  ignoring capitalization and whitespace differences. Similar wording and
  duplicate text in different documents are allowed. Changing the section or
  type does not allow duplicate content within the document.
- Types are optional text labels; the editable dropdown suggests common types and
  labels already used in the current document. **Unspecified** filters for blank
  types. Type labels retain your capitalization.
- Links connect different documents, support multiple parents/children, and
  reject duplicates and circular chains. Deletion removes related links and
  scanned locations after confirmation.
- Changes persist on **Save**. Navigation and closing prompt about unsaved edits.
- Scans find explicit `REQ: ID` tags anywhere in supported text files; they do not
  parse programming languages. Tags are case insensitive; repeat `REQ:` for
  multiple IDs. File locations use absolute paths and one-based line numbers.
- Each scan **replaces the project's previous scan**, so choose a common root
  containing all your source files. Moved projects need rescanning on the new
  machine. Scans run in the background and report unknown tags and read errors.
- Common source files plus `.txt`, `.md`, `.yaml`, `.yml`, and `.toml` are scanned.
  The complete extension and excluded-directory lists are at the bottom of
  `storage.py`. Symlinks, dependency/build directories, non-UTF-8 files, and files
  larger than 2 MB are skipped.
- Coverage indicators describe missing trace links/code tags; they are not
  compliance judgments. Top-level and non-software requirements can have gaps
  intentionally. Scan diagnostics remain in the results dialog, not the matrix.
- Documents use this app's SQLite project format. No Word/PDF import, rich text,
  document deletion, document/section renaming, collaboration, baselines, or audit history yet.
  PDF export uses an embedded Vera font; HTML is preferable for scripts that font
  does not cover.

## Read or extend the code

| File | Responsibility |
| --- | --- |
| `storage.py` | SQLite schema/migration, sections, IDs, types, links, matrix, scanner |
| `export.py` | HTML and PDF matrices grouped by document, section, and type |
| `views.py` | Grouped/sortable requirement table, trace diagram, shared table widget |
| `app.py` | Project lifecycle, shared editor, view registration, dialogs, background scan |
| `test_trace.py` | Storage, scanning, exports, migration, and shared navigation checks |
| `test_views.py` | Grouping, sorting, filtering, trace direction, and selection checks |

Read `storage.py`, then `export.py`, then `views.py` and `app.py`. Add fields in the schema and
storage methods first, expose them in the editor, then include them in `cells()`
in `export.py`. Projects now use schema version 2. Opening a version 1 project
automatically migrates it in a transaction: each document gets a `General` section
with its existing prefix/counter, requirements keep their IDs and gain a blank
type, and links/code locations are preserved. Reopening does not repeat migration.
The original version of the app cannot open upgraded files. There is no packaging
or hidden application state.

To add another tab, create a `ttk.Frame` class in `views.py` accepting
`(parent, on_select, guard)`. Implement `refresh(project, selected)`,
`highlight(requirement_id)`, and `set_enabled(enabled)`. Register it with
`self.add_view("View name", YourView)` in `App.__init__`. Registered views receive
project changes, selection changes, and enabled state automatically. Use
`on_select(requirement_id)` to request selection through the shared editor; a
false result means navigation was cancelled, so retain the previous highlight.
Wrap GUI callbacks with `guard` to use the app's error dialogs. The Requirements
tab also owns its filters and add/section controls. No new dependency or database
migration is needed for these views; project files still use schema version 2.

Run checks with `python -m unittest -v`. The PDF check skips without ReportLab;
the live GUI check skips without a desktop display. Storage, scanning, HTML, and
navigation-controller checks run without a display.
