"""Project storage and code scanning. No GUI or export dependencies."""
import os
from pathlib import Path
import re
import sqlite3


SCHEMA = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL UNIQUE, next_number INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE requirements (
    id TEXT PRIMARY KEY, document INTEGER NOT NULL REFERENCES documents(id),
    title TEXT NOT NULL, description TEXT NOT NULL, fingerprint TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX unique_content ON requirements(document, fingerprint) WHERE deleted=0;
CREATE TABLE links (
    parent TEXT REFERENCES requirements(id), child TEXT REFERENCES requirements(id),
    PRIMARY KEY(parent, child), CHECK(parent != child)
);
CREATE TABLE code_refs (
    requirement TEXT REFERENCES requirements(id), file TEXT, line INTEGER,
    PRIMARY KEY(requirement, file, line)
);
PRAGMA application_id=1381257795;
PRAGMA user_version=1;
"""


# Fresh projects and v1 files take the same atomic migration. The old document
# prefix/counter columns remain for compatibility; sections now allocate IDs.
MIGRATION_2 = """
BEGIN IMMEDIATE;
CREATE TABLE sections (
    id INTEGER PRIMARY KEY, document INTEGER NOT NULL REFERENCES documents(id),
    name TEXT NOT NULL COLLATE NOCASE, prefix TEXT NOT NULL UNIQUE,
    next_number INTEGER NOT NULL DEFAULT 1, UNIQUE(document, name)
);
INSERT INTO sections(document,name,prefix,next_number)
    SELECT id,'General',prefix,next_number FROM documents;
ALTER TABLE requirements ADD COLUMN section INTEGER REFERENCES sections(id);
ALTER TABLE requirements ADD COLUMN type TEXT NOT NULL DEFAULT '';
UPDATE requirements SET section=(SELECT id FROM sections WHERE document=requirements.document);
PRAGMA user_version=2;
COMMIT;
"""


def checked_prefix(prefix):
    prefix = prefix.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]*", prefix):
        raise ValueError("Prefixes must start with a letter and contain letters, digits, _ or -.")
    return prefix


class Project:
    def __init__(self, path, create=False):
        self.path = Path(path).resolve()
        if create and self.path.exists():
            raise ValueError("Choose a new filename; existing projects are never overwritten.")
        mode = "rwc" if create else "rw"
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=" + mode, uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            if create:
                self.db.executescript(SCHEMA)
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if (self.db.execute("PRAGMA application_id").fetchone()[0] != 1381257795
                    or version not in (1, 2)):
                raise ValueError("This is not a supported Requirements Trace project.")
            if version == 1:
                self.db.executescript(MIGRATION_2)
            self.db.execute("SELECT id, title FROM requirements LIMIT 1")
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def documents(self):
        return self.db.execute("SELECT * FROM documents ORDER BY id").fetchall()

    def add_document(self, name, prefix, section_name="General"):
        name, section_name, prefix = name.strip(), section_name.strip(), checked_prefix(prefix)
        if not name or not section_name:
            raise ValueError("Enter a document name and first section name.")
        try:
            with self.db:
                document = self.db.execute("INSERT INTO documents(name,prefix) VALUES (?,?)",
                                           (name, prefix)).lastrowid
                self.db.execute("INSERT INTO sections(document,name,prefix) VALUES (?,?,?)",
                                (document, section_name, prefix))
            return document
        except sqlite3.IntegrityError:
            raise ValueError("That document name or prefix already exists.") from None

    def sections(self, document=None):
        return self.db.execute("SELECT * FROM sections WHERE ? IS NULL OR document=? ORDER BY id",
                               (document, document)).fetchall()

    def add_section(self, document, name, prefix):
        name, prefix = name.strip(), checked_prefix(prefix)
        if not name:
            raise ValueError("Enter a section name.")
        try:
            with self.db:
                return self.db.execute("INSERT INTO sections(document,name,prefix) VALUES (?,?,?)",
                                       (document, name, prefix)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("Choose an existing document, a new section name, and a project-wide unique prefix.") from None

    def requirements(self, document=None, section=None, requirement_type=None):
        return self.db.execute(
            "SELECT r.*, d.name AS document_name, s.name AS section_name, s.prefix AS section_prefix "
            "FROM requirements r JOIN documents d ON d.id=r.document JOIN sections s ON s.id=r.section "
            "WHERE r.deleted=0 AND (? IS NULL OR r.document=?) AND (? IS NULL OR r.section=?) "
            "AND (? IS NULL OR r.type=?) ORDER BY d.id, s.id, r.type COLLATE NOCASE, r.type, r.rowid",
            (document, document, section, section, requirement_type, requirement_type)).fetchall()

    def types(self, document=None):
        return sorted({r["type"] for r in self.requirements(document) if r["type"]}, key=str.casefold)

    def save_requirement(self, document, requirement, title, description, section=None, requirement_type=None):
        title, description = title.strip(), description.strip()
        if not title:
            raise ValueError("A requirement needs a title.")
        fingerprint = repr(tuple(" ".join(s.split()).casefold() for s in (title, description)))
        try:
            with self.db:
                old = None
                if requirement:
                    old = self.db.execute("SELECT * FROM requirements WHERE id=? AND document=? AND deleted=0",
                                          (requirement, document)).fetchone()
                    if old is None:
                        raise ValueError("The requirement no longer exists in this document.")
                if section is None:
                    choices = self.sections(document)
                    section = old["section"] if old else (choices[0]["id"] if choices else None)
                if not self.db.execute("SELECT 1 FROM sections WHERE id=? AND document=?",
                                       (section, document)).fetchone():
                    raise ValueError("Choose a section belonging to this document.")
                if requirement_type is None:
                    requirement_type = old["type"] if old else ""
                requirement_type = requirement_type.strip()
                if old:
                    self.db.execute("UPDATE requirements SET title=?, description=?, fingerprint=?, section=?, type=? "
                                    "WHERE id=?", (title, description, fingerprint, section, requirement_type, requirement))
                else:
                    # A write lock keeps allocation and the counter update atomic.
                    self.db.execute("UPDATE sections SET next_number=next_number WHERE id=?", (section,))
                    group = self.db.execute("SELECT * FROM sections WHERE id=?", (section,)).fetchone()
                    number = group["next_number"]
                    requirement = f"{group['prefix']}{number}"
                    while self.db.execute("SELECT 1 FROM requirements WHERE id=?", (requirement,)).fetchone():
                        number += 1
                        requirement = f"{group['prefix']}{number}"
                    self.db.execute("INSERT INTO requirements(id,document,title,description,fingerprint,section,type) "
                                    "VALUES (?,?,?,?,?,?,?)",
                                    (requirement, document, title, description, fingerprint, section, requirement_type))
                    self.db.execute("UPDATE sections SET next_number=? WHERE id=?", (number + 1, section))
            return requirement
        except sqlite3.IntegrityError:
            raise ValueError("A requirement with the same title and description already exists in this document.") from None

    def delete_requirement(self, requirement):
        with self.db:
            self.db.execute("DELETE FROM links WHERE parent=? OR child=?", (requirement, requirement))
            self.db.execute("DELETE FROM code_refs WHERE requirement=?", (requirement,))
            # Keep a tombstone so another document's prefix cannot recycle this ID.
            self.db.execute("UPDATE requirements SET deleted=1 WHERE id=?", (requirement,))

    def link(self, parent, child):
        rows = {r["id"]: r for r in self.requirements()}
        if parent not in rows or child not in rows:
            raise ValueError("Select two existing requirements.")
        if rows[parent]["document"] == rows[child]["document"]:
            raise ValueError("Choose a requirement from another document.")
        cycle = self.db.execute(
            "WITH RECURSIVE descendants(id) AS (SELECT child FROM links WHERE parent=? "
            "UNION SELECT l.child FROM links l JOIN descendants d ON l.parent=d.id) "
            "SELECT 1 FROM descendants WHERE id=?", (child, parent)).fetchone()
        if cycle:
            raise ValueError("This link would create a circular parent/child chain.")
        try:
            with self.db:
                self.db.execute("INSERT INTO links VALUES (?,?)", (parent, child))
        except sqlite3.IntegrityError:
            raise ValueError("That trace link already exists.") from None

    def unlink(self, parent, child):
        with self.db:
            self.db.execute("DELETE FROM links WHERE parent=? AND child=?", (parent, child))

    def replace_code_refs(self, refs):
        with self.db:
            self.db.execute("DELETE FROM code_refs")
            self.db.executemany("INSERT OR IGNORE INTO code_refs VALUES (?,?,?)", refs)

    def matrix(self):
        rows = {r["id"]: dict(r, parents=[], children=[], code=[]) for r in self.requirements()}
        for parent, child in self.db.execute("SELECT parent, child FROM links ORDER BY parent,child"):
            rows[parent]["children"].append(child)
            rows[child]["parents"].append(parent)
        for requirement, file, line in self.db.execute("SELECT * FROM code_refs ORDER BY file,line"):
            rows[requirement]["code"].append(f"{file}:{line}")
        return list(rows.values())


TAG = re.compile(r"\bREQ:\s*([A-Za-z][A-Za-z0-9_-]*)(?![\w-])", re.IGNORECASE)
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", "target"}
SOURCE_EXTENSIONS = set(".py .pyi .c .h .cpp .hpp .cc .cxx .cs .java .js .jsx .ts .tsx "
                        ".rs .go .swift .kt .kts .m .mm .rb .sh .sql .v .sv .vhd .vhdl "
                        ".txt .md .yaml .yml .toml".split())


def scan_code(folder, known_ids):
    """Return (references, unknown tags, skipped/read errors), without touching SQLite."""
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError("Choose an existing source folder.")
    refs, unknown, skipped = [], [], []
    for directory, dirs, files in os.walk(root, onerror=lambda error: skipped.append(str(error))):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not Path(directory, d).is_symlink())
        for name in sorted(files):
            path = Path(directory, name)
            if path.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            try:
                if path.is_symlink() or path.stat().st_size > 2_000_000:
                    skipped.append(f"{path}: symlink or larger than 2 MB")
                    continue
                for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    for tag in TAG.findall(text):
                        tag = tag.upper()
                        if tag in known_ids:
                            refs.append((tag, str(path), line))
                        else:
                            unknown.append(f"{path}:{line}: {tag}")
            except (OSError, UnicodeError) as error:
                skipped.append(f"{path}: {error}")
    return sorted(set(refs)), unknown, skipped
