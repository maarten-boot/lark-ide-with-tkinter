#!/usr/bin/env python3
"""lark-ide - a three pane tkinter workbench for developing Lark grammars.

Left pane:   the Lark grammar.
Middle pane: the text to be parsed with that grammar.
Right pane:  the resulting parse tree, or the error explaining why there is none.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

try:
    import lark
    from lark import exceptions as lark_exceptions
except ImportError:  # pragma: no cover - exercised only when lark is absent
    lark = None
    lark_exceptions = None

APP_NAME = "lark-ide"
WINDOW_GEOMETRY = "1400x850"

GRAMMAR_FILETYPES = [("Lark grammars", "*.lark"), ("All files", "*.*")]
INPUT_FILETYPES = [("Text files", "*.txt"), ("All files", "*.*")]

AUTO_PARSE_DELAY_MS = 400
PARSERS = ("earley", "lalr")
DEFAULT_PARSER = "earley"
DEFAULT_START_RULE = "start"

ERROR_TAG = "error"
SPAN_TAG = "span"
SPAN_COLOUR = "#cfe3ff"
ERROR_COLOUR = "#b00020"
OK_COLOUR = "#1b5e20"
HIGHLIGHT_COLOUR = "#ffd6d6"

CONFIG_DIR = Path.home() / f".{APP_NAME}"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
MAX_RECENT = 20

GRAMMAR_KIND = "grammar"
INPUT_KIND = "input"

TEXT_VIEW = "text"
TREE_VIEW = "tree"
CORPUS_VIEW = "corpus"
VIEW_ORDER = (TEXT_VIEW, TREE_VIEW, CORPUS_VIEW)
MAX_TREE_NODES = 20000
MAX_TREE_VALUE_CHARS = 80

CORPUS_SUFFIX = ".corpus.json"
CORPUS_VERSION = 1
CORPUS_FILETYPES = [("Corpus files", "*.json"), ("All files", "*.*")]
EXPECT_PARSE = "parse"
EXPECT_ERROR = "error"
RESULT_UNKNOWN = ""
RESULT_PASS = "pass"
RESULT_FAIL = "fail"
PASS_COLOUR = "#1b5e20"

HIGHLIGHT_DELAY_MS = 150
SYNTAX_COLOURS = {
    "comment": "#6a737d",
    "directive": "#a626a4",
    "string": "#50a14f",
    "regexp": "#c18401",
    "terminal": "#986801",
    "rule": "#4078f2",
    "number": "#986801",
    "operator": "#e45649",
}
# Mirrors lark.load_grammar.TERMINALS closely enough for colouring; order of alternatives matters.
SYNTAX_PATTERN = re.compile(
    r"(?P<comment>//[^\n]*|\#[^\n]*)"
    r"|(?P<string>\"(?:\\\"|\\\\|[^\"\n])*?\"i?)"
    r"|(?P<regexp>/(?!/)(?:\\/|\\\\|[^/\n])*?/[imslux]*)"
    r"|(?P<directive>%(?:ignore|import|declare|override|extend))"
    r"|(?P<terminal>_?[A-Z][_A-Z0-9]*)"
    r"|(?P<rule>_?[a-z][_a-z0-9]*)"
    r"|(?P<number>[+-]?\d+)"
    r"|(?P<operator>->|[:|~?*+()\[\]{}])"
)


class Settings:
    """Application settings persisted as JSON under the config directory."""

    def __init__(self, path: Path | None = None) -> None:
        # Resolved on each call, not bound as a default argument, so the location stays overridable.
        self.path = path if path is not None else SETTINGS_PATH
        self.data: dict = {}
        self.load()

    def load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        self.data = loaded if isinstance(loaded, dict) else {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            return  # settings are a convenience, never a reason to interrupt the user

    def get(self, key: str, default):
        value = self.data.get(key, default)
        return value if isinstance(value, type(default)) else default

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def recent(self, kind: str) -> list[str]:
        """The most-recently-used paths for a kind, newest first, existing files only."""
        values = self.data.get(f"recent_{kind}")
        if not isinstance(values, list):
            return []
        return [v for v in values if isinstance(v, str) and Path(v).is_file()][:MAX_RECENT]

    def remember(self, kind: str, path: Path) -> None:
        entry = str(path.expanduser().resolve())
        others = [p for p in self.recent(kind) if p != entry]
        self.data[f"recent_{kind}"] = [entry, *others][:MAX_RECENT]

    def forget_all(self, kind: str) -> None:
        self.data[f"recent_{kind}"] = []


class GrammarHighlighter:
    """Regex based colouring of Lark grammar source in a text widget."""

    def __init__(self, text: ScrolledText) -> None:
        self.text = text
        for name, colour in SYNTAX_COLOURS.items():
            text.tag_configure(name, foreground=colour)

    def highlight(self) -> None:
        content = self.text.get("1.0", "end-1c")
        for name in SYNTAX_COLOURS:
            self.text.tag_remove(name, "1.0", "end")
        for match in SYNTAX_PATTERN.finditer(content):
            name = match.lastgroup
            if name is None:
                continue
            start = f"1.0 + {match.start()}c"
            self.text.tag_add(name, start, f"1.0 + {match.end()}c")


@dataclass
class CorpusCase:
    """One named sample of the language, with what it is expected to do."""

    name: str
    text: str
    expect: str = EXPECT_PARSE
    tree: str | None = None
    result: str = field(default=RESULT_UNKNOWN, compare=False)
    detail: str = field(default="", compare=False)

    def to_dict(self) -> dict:
        data = {"name": self.name, "input": self.text, "expect": self.expect}
        if self.tree is not None:
            data["tree"] = self.tree
        return data

    @classmethod
    def from_dict(cls, data: dict) -> CorpusCase:
        expect = data.get("expect", EXPECT_PARSE)
        tree = data.get("tree")
        return cls(
            name=str(data.get("name", "unnamed")),
            text=str(data.get("input", "")),
            expect=expect if expect in (EXPECT_PARSE, EXPECT_ERROR) else EXPECT_PARSE,
            tree=str(tree) if isinstance(tree, str) else None,
        )


class Corpus:
    """A list of test inputs for one grammar, stored alongside it as JSON."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.cases: list[CorpusCase] = []
        self.dirty = False

    @staticmethod
    def default_path_for(grammar_path: Path) -> Path:
        return grammar_path.with_suffix(CORPUS_SUFFIX)

    def clear(self) -> None:
        self.path = None
        self.cases = []
        self.dirty = False

    def load(self, path: Path) -> None:
        """Replace the contents from a file. Raises OSError or ValueError on a bad file."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload.get("cases") if isinstance(payload, dict) else None
        entries = list(raw) if isinstance(raw, list) else None
        if entries is None:
            raise ValueError("a corpus file must be a JSON object holding a 'cases' list")
        self.cases = [CorpusCase.from_dict(case) for case in entries if isinstance(case, dict)]
        self.path = path
        self.dirty = False

    def save(self, path: Path | None = None) -> None:
        target = path or self.path
        if target is None:
            raise ValueError("no corpus path to save to")
        payload = {"version": CORPUS_VERSION, "cases": [case.to_dict() for case in self.cases]}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.path = target
        self.dirty = False

    def add(self, case: CorpusCase) -> None:
        self.cases.append(case)
        self.dirty = True

    def remove(self, index: int) -> None:
        del self.cases[index]
        self.dirty = True

    def unique_name(self, wanted: str) -> str:
        existing = {case.name for case in self.cases}
        if wanted not in existing:
            return wanted
        for suffix in range(2, len(existing) + 3):
            candidate = f"{wanted} ({suffix})"
            if candidate not in existing:
                return candidate
        return wanted


def node_span(node: object) -> tuple[int, int, int, int] | None:
    """The (line, column, end_line, end_column) a tree or token covers, or None when unknown."""
    meta = getattr(node, "meta", None)
    if meta is not None and not getattr(meta, "empty", True):
        return meta.line, meta.column, meta.end_line, meta.end_column
    line = getattr(node, "line", None)
    end_line = getattr(node, "end_line", None)
    if line is None or end_line is None:
        return None
    return line, node.column, end_line, node.end_column


def run_corpus(parser: object, cases: list[CorpusCase]) -> tuple[int, int]:
    """Run every case against a compiled parser, recording the outcome on each. Returns (passed, total)."""
    passed = 0
    for case in cases:
        case.result, case.detail = _run_case(parser, case)
        if case.result == RESULT_PASS:
            passed += 1
    return passed, len(cases)


def _run_case(parser: object, case: CorpusCase) -> tuple[str, str]:
    try:
        tree = parser.parse(case.text)
    except lark_exceptions.LarkError as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        if case.expect == EXPECT_ERROR:
            return RESULT_PASS, first_line
        return RESULT_FAIL, first_line
    if case.expect == EXPECT_ERROR:
        return RESULT_FAIL, "parsed, but an error was expected"
    if case.tree is not None and tree.pretty().rstrip("\n") != case.tree.rstrip("\n"):
        return RESULT_FAIL, "tree differs from the recorded one"
    return RESULT_PASS, ""


class EditorPane(ttk.Frame):
    """A titled, scrollable, editable text pane backed by an optional file on disk."""

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        filetypes: list[tuple[str, str]],
        default_extension: str,
        editor_font: tkfont.Font,
        on_change: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.base_title = title
        self.filetypes = filetypes
        self.default_extension = default_extension
        self.on_change = on_change
        self.path: Path | None = None
        self.dirty = False

        header = ttk.Frame(self)
        header.pack(fill="x")
        self.header = ttk.Label(header, anchor="w", padding=(4, 3))
        self.header.pack(side="left", fill="x", expand=True)
        self.badge = ttk.Label(header, anchor="e", padding=(4, 3))
        self.badge.pack(side="right")

        self.text = ScrolledText(self, wrap="none", undo=True, font=editor_font, width=40, height=25)
        self.text.pack(fill="both", expand=True)

        hbar = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        hbar.pack(fill="x")
        self.text.configure(xscrollcommand=hbar.set)
        self.text.tag_configure(ERROR_TAG, background=HIGHLIGHT_COLOUR)
        self.text.tag_configure(SPAN_TAG, background=SPAN_COLOUR)
        self.text.tag_lower(SPAN_TAG, ERROR_TAG)

        self.text.bind("<<Modified>>", self._on_modified)
        self._build_context_menu()
        self._refresh_header()

    # -- context menu ----------------------------------------------------------------------------

    def _build_context_menu(self) -> None:
        """File commands first, then the usual editing commands. File entries are filled in later."""
        self.menu = tk.Menu(self, tearoff=False)
        self.file_command_count = 0
        self.menu.add_command(label="Cut", command=lambda: self._edit_event("<<Cut>>"))
        self.menu.add_command(label="Copy", command=lambda: self._edit_event("<<Copy>>"))
        self.menu.add_command(label="Paste", command=lambda: self._edit_event("<<Paste>>"))
        self.menu.add_separator()
        self.menu.add_command(label="Select All", command=self.select_all)
        self.menu.add_separator()
        self.menu.add_command(label="Undo", command=lambda: self._edit_event("<<Undo>>"))
        self.menu.add_command(label="Redo", command=lambda: self._edit_event("<<Redo>>"))
        self.text.bind("<Button-3>", self._on_context_menu)
        self.text.bind("<Button-2>", self._on_context_menu)

    def set_file_commands(self, commands: list[tuple[str, Callable[[], None]]]) -> None:
        """Insert file commands at the top of the context menu, replacing any set before."""
        for _ in range(self.file_command_count):
            self.menu.delete(0)
        for index, (label, callback) in enumerate(commands):
            self.menu.insert_command(index, label=label, command=callback)
        self.menu.insert_separator(len(commands))
        self.file_command_count = len(commands) + 1

    def _edit_event(self, event: str) -> None:
        self.text.event_generate(event)

    def select_all(self) -> None:
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.focus_set()

    def _on_context_menu(self, event: tk.Event) -> str:
        self.text.focus_set()
        self.menu.tk_popup(event.x_root, event.y_root)
        return "break"

    # -- content ---------------------------------------------------------------------------------

    def content(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_content(self, value: str) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.edit_reset()
        self.mark_clean()

    def mark_clean(self) -> None:
        self.dirty = False
        self.text.edit_modified(False)
        self._refresh_header()

    def set_badge(self, text: str) -> None:
        self.badge.configure(text=text)

    def clear_error(self) -> None:
        self.text.tag_remove(ERROR_TAG, "1.0", "end")

    def clear_span(self) -> None:
        self.text.tag_remove(SPAN_TAG, "1.0", "end")

    def highlight_span(self, span: tuple[int, int, int, int]) -> None:
        """Mark the region a parse tree node covers. Positions are 1-based, as lark reports them."""
        line, column, end_line, end_column = span
        start = f"{line}.{max(column - 1, 0)}"
        end = f"{end_line}.{max(end_column - 1, 0)}"
        self.clear_span()
        self.text.tag_add(SPAN_TAG, start, end)
        self.text.see(start)

    def highlight_error(self, line: int, column: int) -> None:
        start = f"{line}.{max(column - 1, 0)}"
        self.text.tag_add(ERROR_TAG, start, f"{start} +1c")
        self.text.see(start)

    def _on_modified(self, _event: tk.Event | None = None) -> None:
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        self.dirty = True
        self._refresh_header()
        self.on_change()

    def _refresh_header(self) -> None:
        name = self.path.name if self.path else "(unsaved)"
        marker = " *" if self.dirty else ""
        self.header.configure(text=f"{self.base_title} - {name}{marker}")

    # -- files -----------------------------------------------------------------------------------

    def load_path(self, path: Path) -> bool:
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Cannot read {path}:\n{exc}", parent=self)
            return False
        self.set_content(value)
        self.path = path
        self._refresh_header()
        return True

    def open_file(self) -> bool:
        if not self.confirm_discard():
            return False
        chosen = filedialog.askopenfilename(
            parent=self,
            title=f"Open {self.base_title.lower()}",
            filetypes=self.filetypes,
            initialdir=str(self.path.parent) if self.path else None,
        )
        return self.load_path(Path(chosen)) if chosen else False

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        try:
            self.path.write_text(self.content(), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Cannot write {self.path}:\n{exc}", parent=self)
            return False
        self.mark_clean()
        return True

    def save_as(self) -> bool:
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title=f"Save {self.base_title.lower()} as",
            filetypes=self.filetypes,
            defaultextension=self.default_extension,
            initialfile=self.path.name if self.path else None,
            initialdir=str(self.path.parent) if self.path else None,
        )
        if not chosen:
            return False
        self.path = Path(chosen)
        return self.save()

    def new_file(self) -> bool:
        if not self.confirm_discard():
            return False
        self.set_content("")
        self.path = None
        self._refresh_header()
        return True

    def confirm_discard(self) -> bool:
        """Return True when it is safe to replace the pane contents."""
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            APP_NAME,
            f"{self.base_title} has unsaved changes.\nSave them first?",
            parent=self,
        )
        if answer is None:
            return False
        return self.save() if answer else True


class CorpusView(ttk.Frame):
    """The Corpus tab: every case with its last result."""

    def __init__(self, master: tk.Misc, on_open_case: Callable[[int], None]) -> None:
        super().__init__(master)
        self.on_open_case = on_open_case

        self.summary = ttk.Label(self, anchor="w", padding=(4, 3), text="No corpus loaded")
        self.summary.pack(fill="x")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("expect", "result", "detail"), show="headings", selectmode="browse")
        for column, heading, width in (
            ("expect", "Expect", 70),
            ("result", "Result", 70),
            ("detail", "Detail", 240),
        ):
            self.tree.heading(column, text=heading, anchor="w")
            self.tree.column(column, width=width, minwidth=60, stretch=column == "detail")
        self.tree["columns"] = ("name", "expect", "result", "detail")
        self.tree.heading("name", text="Case", anchor="w")
        self.tree.column("name", width=180, minwidth=80, stretch=True)
        self.tree.tag_configure(RESULT_PASS, foreground=PASS_COLOUR)
        self.tree.tag_configure(RESULT_FAIL, foreground=ERROR_COLOUR)

        vbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        hbar = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self._on_double_click)

    def show(self, corpus: Corpus, summary: str) -> None:
        selected = self.selected_index()
        self.tree.delete(*self.tree.get_children())
        for index, case in enumerate(corpus.cases):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(case.name, case.expect, case.result, case.detail),
                tags=(case.result,) if case.result else (),
            )
        if selected is not None and 0 <= selected < len(corpus.cases):
            self.tree.selection_set(str(selected))
        name = corpus.path.name if corpus.path else "(unsaved)"
        marker = " *" if corpus.dirty else ""
        self.summary.configure(text=f"{name}{marker} - {summary}")

    def selected_index(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _on_double_click(self, _event: tk.Event) -> str:
        index = self.selected_index()
        if index is not None:
            self.on_open_case(index)
        return "break"


class ResultPane(ttk.Frame):
    """The right hand pane: the parse result as pretty text, as a collapsible tree, and the corpus."""

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        editor_font: tkfont.Font,
        on_view_change: Callable[[], None],
        on_open_case: Callable[[int], None],
        on_node_selected: Callable[[object | None], None],
    ) -> None:
        super().__init__(master)
        self.base_title = title
        self.on_view_change = on_view_change
        self.on_node_selected = on_node_selected
        self.pretty = ""
        self.nodes: dict[str, object] = {}

        self.header = ttk.Label(self, anchor="w", padding=(4, 3), text=title)
        self.header.pack(fill="x")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.add(self._build_text_view(editor_font), text="Text")
        self.notebook.add(self._build_tree_view(), text="Tree")
        self.corpus_view = CorpusView(self.notebook, on_open_case)
        self.notebook.add(self.corpus_view, text="Corpus")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self.on_view_change())

        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="Copy", command=self.copy_selection)
        self.menu.add_command(label="Select All", command=self.select_all)

    def _build_text_view(self, editor_font: tkfont.Font) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)
        self.text = ScrolledText(frame, wrap="none", font=editor_font, width=40, height=25)
        self.text.pack(fill="both", expand=True)
        hbar = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        hbar.pack(fill="x")
        self.text.configure(xscrollcommand=hbar.set, state="disabled")
        self.text.tag_configure(ERROR_TAG, foreground=ERROR_COLOUR)
        self.text.bind("<Control-c>", self._on_copy)
        self.text.bind("<Control-a>", self._on_select_all)
        self.text.bind("<Button-3>", self._on_context_menu)
        self.text.bind("<Button-2>", self._on_context_menu)
        return frame

    def _build_tree_view(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)
        self.tree = ttk.Treeview(frame, columns=("value", "pos"), selectmode="browse")
        self.tree.heading("#0", text="Node", anchor="w")
        self.tree.heading("value", text="Value", anchor="w")
        self.tree.heading("pos", text="Line:Col", anchor="e")
        self.tree.column("#0", width=240, minwidth=120, stretch=True)
        self.tree.column("value", width=200, minwidth=80, stretch=True)
        self.tree.column("pos", width=80, minwidth=60, stretch=False, anchor="e")

        vbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hbar = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.tree.bind("<Control-c>", self._on_copy)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-3>", self._on_context_menu)
        self.tree.bind("<Button-2>", self._on_context_menu)
        return frame

    def _on_tree_select(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        self.on_node_selected(self.nodes.get(selection[0]) if selection else None)

    # -- views -----------------------------------------------------------------------------------

    def current_view(self) -> str:
        index = self.notebook.index("current")
        return VIEW_ORDER[index] if 0 <= index < len(VIEW_ORDER) else TEXT_VIEW

    def select_view(self, view: str) -> None:
        self.notebook.select(VIEW_ORDER.index(view) if view in VIEW_ORDER else 0)

    # -- content ---------------------------------------------------------------------------------

    def show_tree(self, tree: object, pretty: str) -> bool:
        """Render a parse tree in both views. Returns False when the tree view was truncated."""
        self.pretty = pretty
        self._set_text(pretty, is_error=False)
        return self._fill_tree(tree)

    def show_error(self, message: str) -> None:
        self.pretty = message
        self._set_text(message, is_error=True)
        self._clear_tree()
        self.select_view(TEXT_VIEW)

    def clear(self) -> None:
        self.pretty = ""
        self._set_text("", is_error=False)
        self._clear_tree()

    def _set_text(self, value: str, is_error: bool) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        if is_error:
            self.text.tag_add(ERROR_TAG, "1.0", "end")
        self.text.configure(state="disabled")
        self.text.yview_moveto(0.0)

    def _clear_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.nodes.clear()

    def _fill_tree(self, node: object) -> bool:
        self._clear_tree()
        complete = self._insert_node("", node, [0])
        if not complete:
            self.tree.insert("", "end", text=f"... truncated at {MAX_TREE_NODES} nodes")
        self.expand_all()
        return complete

    def _insert_node(self, parent: str, node: object, counter: list[int]) -> bool:
        if counter[0] >= MAX_TREE_NODES:
            return False
        counter[0] += 1
        children = getattr(node, "children", None)
        if children is None:
            label, value, position = self._describe_token(node)
        else:
            label, value, position = self._describe_rule(node)
        item = self.tree.insert(parent, "end", text=label, values=(value, position))
        self.nodes[item] = node
        for child in children or ():
            if not self._insert_node(item, child, counter):
                return False
        return True

    @staticmethod
    def _describe_rule(node: object) -> tuple[str, str, str]:
        meta = getattr(node, "meta", None)
        position = ""
        if meta is not None and not getattr(meta, "empty", True):
            position = f"{meta.line}:{meta.column}"
        return str(getattr(node, "data", node)), "", position

    @staticmethod
    def _describe_token(token: object) -> tuple[str, str, str]:
        value = str(token).replace("\n", "\\n").replace("\t", "\\t")
        if len(value) > MAX_TREE_VALUE_CHARS:
            value = value[: MAX_TREE_VALUE_CHARS - 3] + "..."
        line = getattr(token, "line", None)
        column = getattr(token, "column", None)
        position = f"{line}:{column}" if line and column else ""
        return str(getattr(token, "type", "TOKEN")), value, position

    def expand_all(self) -> None:
        for item in self._walk():
            self.tree.item(item, open=True)

    def collapse_all(self) -> None:
        for item in self._walk():
            self.tree.item(item, open=False)

    def _walk(self, parent: str = "") -> list[str]:
        items = []
        for item in self.tree.get_children(parent):
            items.append(item)
            items.extend(self._walk(item))
        return items

    # -- clipboard -------------------------------------------------------------------------------

    def copy_selection(self) -> None:
        value = self._selected_tree_text() if self.current_view() == TREE_VIEW else self._selected_text()
        if value:
            self.clipboard_clear()
            self.clipboard_append(value)

    def _selected_text(self) -> str:
        try:
            return self.text.get("sel.first", "sel.last")
        except tk.TclError:
            return self.text.get("1.0", "end-1c")

    def _selected_tree_text(self) -> str:
        selection = self.tree.selection()
        if not selection:
            return self.pretty
        node = self.nodes.get(selection[0])
        pretty = getattr(node, "pretty", None)
        return pretty() if callable(pretty) else str(node)

    def select_all(self) -> None:
        if self.current_view() == TREE_VIEW:
            return
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.focus_set()

    def _on_copy(self, _event: tk.Event) -> str:
        self.copy_selection()
        return "break"

    def _on_select_all(self, _event: tk.Event) -> str:
        self.select_all()
        return "break"

    def _on_context_menu(self, event: tk.Event) -> str:
        self.menu.tk_popup(event.x_root, event.y_root)
        return "break"


class StatusBar(ttk.Frame):
    """The single line of status at the bottom of the window."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.message = ttk.Label(self, anchor="w", padding=(6, 3))
        self.message.pack(side="left", fill="x", expand=True)
        self.detail = ttk.Label(self, anchor="e", padding=(6, 3))
        self.detail.pack(side="right")

    def set_message(self, text: str, is_error: bool = False) -> None:
        self.message.configure(text=text, foreground=ERROR_COLOUR if is_error else OK_COLOUR)

    def set_detail(self, text: str, is_error: bool = False) -> None:
        self.detail.configure(text=text, foreground=ERROR_COLOUR if is_error else OK_COLOUR)


class LarkIde(tk.Tk):
    """The application window: menu bar, three panes, status line."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.title(APP_NAME)
        self.geometry(self.settings.get("geometry", WINDOW_GEOMETRY))
        self.minsize(900, 500)

        self.editor_font = tkfont.nametofont("TkFixedFont").copy()
        self.auto_parse = tk.BooleanVar(value=self.settings.get("auto_parse", True))
        self.parser_name = tk.StringVar(value=self.settings.get("parser", DEFAULT_PARSER))
        self.start_rule = tk.StringVar(value=self.settings.get("start_rule", DEFAULT_START_RULE))
        self.result_view = tk.StringVar(value=self.settings.get("result_view", TEXT_VIEW))
        self.run_corpus_on_parse = tk.BooleanVar(value=self.settings.get("run_corpus_on_parse", True))
        self.corpus = Corpus()
        self._corpus_summary = ""
        self._last_parse_succeeded = False
        self._parsed_source: str | None = None
        self._parse_job: str | None = None
        self._highlight_job: str | None = None
        self._restore_job: str | None = None

        self._build_body()
        self._build_menu()
        self._build_bindings()
        self.protocol("WM_DELETE_WINDOW", self.on_quit)
        self.result_pane.select_view(self.result_view.get())
        self.refresh_corpus_view("no cases")
        self._restore_job = self.after(120, self._restore_sashes)
        self._refresh_parser_badge()
        self.status.set_message("Ready" if lark else "The 'lark' package is not installed", is_error=lark is None)

    # -- construction ----------------------------------------------------------------------------

    def _build_body(self) -> None:
        self.panes = ttk.PanedWindow(self, orient="horizontal")
        self.panes.pack(fill="both", expand=True, padx=4, pady=(4, 0))

        self.grammar_pane = EditorPane(
            self.panes, "Grammar", GRAMMAR_FILETYPES, ".lark", self.editor_font, self.on_grammar_changed
        )
        self.input_pane = EditorPane(
            self.panes, "Input", INPUT_FILETYPES, ".txt", self.editor_font, self.schedule_parse
        )
        self.result_pane = ResultPane(
            self.panes,
            "Parse tree",
            self.editor_font,
            self.on_view_changed,
            self.open_corpus_case,
            self.on_node_selected,
        )
        self.highlighter = GrammarHighlighter(self.grammar_pane.text)

        for pane in (self.grammar_pane, self.input_pane, self.result_pane):
            self.panes.add(pane, weight=1)

        self.grammar_pane.set_file_commands(
            [
                ("New Grammar", self.grammar_pane.new_file),
                ("Open Grammar...", self.open_grammar),
                ("Save Grammar", self.save_grammar),
                ("Save Grammar As...", self.save_grammar_as),
            ]
        )
        self.input_pane.set_file_commands(
            [
                ("New Input", self.input_pane.new_file),
                ("Open Input...", self.open_input),
                ("Save Input", self.save_input),
                ("Save Input As...", self.save_input_as),
                ("Add Input as Case...", self.add_case),
            ]
        )

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(4, 0))
        self.status = StatusBar(self)
        self.status.pack(fill="x")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        self.recent_menus = {kind: tk.Menu(file_menu, tearoff=False) for kind in (GRAMMAR_KIND, INPUT_KIND)}

        file_menu.add_command(label="New Grammar", command=self.grammar_pane.new_file)
        file_menu.add_command(label="Open Grammar...", accelerator="Ctrl+O", command=self.open_grammar)
        file_menu.add_cascade(label="Recent Grammars", menu=self.recent_menus[GRAMMAR_KIND])
        file_menu.add_command(label="Save Grammar", accelerator="Ctrl+S", command=self.save_grammar)
        file_menu.add_command(label="Save Grammar As...", command=self.save_grammar_as)
        file_menu.add_separator()
        file_menu.add_command(label="New Input", command=self.input_pane.new_file)
        file_menu.add_command(label="Open Input...", accelerator="Ctrl+Shift+O", command=self.open_input)
        file_menu.add_cascade(label="Recent Inputs", menu=self.recent_menus[INPUT_KIND])
        file_menu.add_command(label="Save Input", accelerator="Ctrl+Shift+S", command=self.save_input)
        file_menu.add_command(label="Save Input As...", command=self.save_input_as)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.on_quit)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Copy Parse Tree", command=self.copy_parse_tree)
        edit_menu.add_command(label="Clear Parse Tree", command=self.clear_parse_tree)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        parse_menu = tk.Menu(menubar, tearoff=False)
        parse_menu.add_command(label="Parse Now", accelerator="F5", command=self.parse_now)
        parse_menu.add_checkbutton(
            label="Parse While Typing", variable=self.auto_parse, command=self.on_auto_parse_changed
        )
        parse_menu.add_separator()
        for name in PARSERS:
            parse_menu.add_radiobutton(
                label=f"Parser: {name}", value=name, variable=self.parser_name, command=self.on_parser_changed
            )
        parse_menu.add_separator()
        parse_menu.add_command(label="Set Start Rule...", command=self.ask_start_rule)
        menubar.add_cascade(label="Parse", menu=parse_menu)

        corpus_menu = tk.Menu(menubar, tearoff=False)
        corpus_menu.add_command(label="New Corpus", command=self.new_corpus)
        corpus_menu.add_command(label="Open Corpus...", command=self.open_corpus)
        corpus_menu.add_command(label="Save Corpus", command=self.save_corpus)
        corpus_menu.add_command(label="Save Corpus As...", command=self.save_corpus_as)
        corpus_menu.add_separator()
        corpus_menu.add_command(label="Add Input as Case...", command=self.add_case)
        corpus_menu.add_command(label="Add Input as Error Case...", command=self.add_error_case)
        corpus_menu.add_command(label="Record Expected Tree", command=self.record_expected_tree)
        corpus_menu.add_command(label="Clear Expected Tree", command=self.clear_expected_tree)
        corpus_menu.add_command(label="Delete Case", command=self.delete_case)
        corpus_menu.add_separator()
        corpus_menu.add_command(label="Run Corpus", accelerator="F6", command=self.run_corpus_now)
        corpus_menu.add_checkbutton(
            label="Run On Every Parse", variable=self.run_corpus_on_parse, command=self.on_run_corpus_changed
        )
        menubar.add_cascade(label="Corpus", menu=corpus_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_radiobutton(
            label="Text View", value=TEXT_VIEW, variable=self.result_view, command=self.on_view_selected
        )
        view_menu.add_radiobutton(
            label="Tree View", value=TREE_VIEW, variable=self.result_view, command=self.on_view_selected
        )
        view_menu.add_radiobutton(
            label="Corpus View", value=CORPUS_VIEW, variable=self.result_view, command=self.on_view_selected
        )
        view_menu.add_separator()
        view_menu.add_command(label="Expand All", command=self.result_pane.expand_all)
        view_menu.add_command(label="Collapse All", command=self.result_pane.collapse_all)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.configure(menu=menubar)
        for kind in (GRAMMAR_KIND, INPUT_KIND):
            self._rebuild_recent_menu(kind)

    def _build_bindings(self) -> None:
        self.bind_all("<Control-o>", lambda _event: self.open_grammar())
        self.bind_all("<Control-O>", lambda _event: self.open_input())
        self.bind_all("<Control-s>", lambda _event: self.grammar_pane.save())
        self.bind_all("<Control-S>", lambda _event: self.input_pane.save())
        self.bind_all("<Control-q>", lambda _event: self.on_quit())
        self.bind_all("<F5>", lambda _event: self.parse_now())
        self.bind_all("<F6>", lambda _event: self.run_corpus_now())

    # -- commands --------------------------------------------------------------------------------

    def open_grammar(self) -> None:
        if self.grammar_pane.open_file():
            self._after_file_action(GRAMMAR_KIND)

    def open_input(self) -> None:
        if self.input_pane.open_file():
            self._after_file_action(INPUT_KIND)

    def save_grammar(self) -> None:
        if self.grammar_pane.save():
            self._remember(GRAMMAR_KIND)

    def save_grammar_as(self) -> None:
        if self.grammar_pane.save_as():
            self._remember(GRAMMAR_KIND)

    def save_input(self) -> None:
        if self.input_pane.save():
            self._remember(INPUT_KIND)

    def save_input_as(self) -> None:
        if self.input_pane.save_as():
            self._remember(INPUT_KIND)

    def open_recent(self, kind: str, path: Path) -> None:
        pane = self.grammar_pane if kind == GRAMMAR_KIND else self.input_pane
        if pane.confirm_discard() and pane.load_path(path):
            self._after_file_action(kind)

    def clear_recent(self, kind: str) -> None:
        self.settings.forget_all(kind)
        self.settings.save()
        self._rebuild_recent_menu(kind)

    def _after_file_action(self, kind: str) -> None:
        self._remember(kind)
        if kind == GRAMMAR_KIND:
            self.highlighter.highlight()
            self._autoload_corpus()
        self.parse_now()

    def _remember(self, kind: str) -> None:
        pane = self.grammar_pane if kind == GRAMMAR_KIND else self.input_pane
        if pane.path is not None:
            self.settings.remember(kind, pane.path)
            self.settings.save()
            self._rebuild_recent_menu(kind)

    def _rebuild_recent_menu(self, kind: str) -> None:
        menu = self.recent_menus[kind]
        menu.delete(0, "end")
        entries = self.settings.recent(kind)
        for entry in entries:
            path = Path(entry)
            menu.add_command(label=str(path), command=lambda p=path, k=kind: self.open_recent(k, p))
        if not entries:
            menu.add_command(label="(empty)", state="disabled")
            return
        menu.add_separator()
        menu.add_command(label="Clear List", command=lambda k=kind: self.clear_recent(k))

    def copy_parse_tree(self) -> None:
        self.result_pane.copy_selection()
        self.status.set_message("Parse tree copied to the clipboard")

    # -- corpus ----------------------------------------------------------------------------------

    def new_corpus(self) -> None:
        if not self.confirm_corpus_discard():
            return
        self.corpus.clear()
        self.refresh_corpus_view("no cases")

    def open_corpus(self) -> None:
        if not self.confirm_corpus_discard():
            return
        chosen = filedialog.askopenfilename(
            parent=self, title="Open corpus", filetypes=CORPUS_FILETYPES, defaultextension=CORPUS_SUFFIX
        )
        if chosen:
            self.load_corpus(Path(chosen), announce=True)

    def load_corpus(self, path: Path, announce: bool) -> bool:
        try:
            self.corpus.load(path)
        except (OSError, ValueError) as exc:
            if announce:
                messagebox.showerror(APP_NAME, f"Cannot read {path}:\n{exc}", parent=self)
            return False
        self.run_corpus_now()
        return True

    def save_corpus(self) -> None:
        if self.corpus.path is None:
            self.save_corpus_as()
            return
        self._write_corpus(self.corpus.path)

    def save_corpus_as(self) -> None:
        suggested = self.corpus.path
        if suggested is None and self.grammar_pane.path is not None:
            suggested = Corpus.default_path_for(self.grammar_pane.path)
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title="Save corpus as",
            filetypes=CORPUS_FILETYPES,
            defaultextension=CORPUS_SUFFIX,
            initialfile=suggested.name if suggested else None,
            initialdir=str(suggested.parent) if suggested else None,
        )
        if chosen:
            self._write_corpus(Path(chosen))

    def _write_corpus(self, path: Path) -> bool:
        try:
            self.corpus.save(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_NAME, f"Cannot write {path}:\n{exc}", parent=self)
            return False
        self.refresh_corpus_view(self._corpus_summary or "not run")
        self.status.set_message(f"Corpus saved to {path.name}")
        return True

    def confirm_corpus_discard(self) -> bool:
        if not self.corpus.dirty:
            return True
        answer = messagebox.askyesnocancel(APP_NAME, "The corpus has unsaved changes.\nSave them first?", parent=self)
        if answer is None:
            return False
        if answer:
            self.save_corpus()
            return not self.corpus.dirty
        return True

    def add_case(self) -> None:
        self._add_case(EXPECT_PARSE)

    def add_error_case(self) -> None:
        self._add_case(EXPECT_ERROR)

    def _add_case(self, expect: str) -> None:
        text = self.input_pane.content()
        if not text:
            self.status.set_message("The input pane is empty, nothing to add", is_error=True)
            return
        default = self.input_pane.path.stem if self.input_pane.path else f"case {len(self.corpus.cases) + 1}"
        name = simpledialog.askstring(APP_NAME, "Case name:", initialvalue=default, parent=self)
        if not name or not name.strip():
            return
        tree = self.result_pane.pretty if expect == EXPECT_PARSE and self._last_parse_succeeded else None
        self.corpus.add(CorpusCase(name=self.corpus.unique_name(name.strip()), text=text, expect=expect, tree=tree))
        self.run_corpus_now()
        self.result_pane.select_view(CORPUS_VIEW)

    def selected_case(self) -> CorpusCase | None:
        index = self.result_pane.corpus_view.selected_index()
        if index is None or not 0 <= index < len(self.corpus.cases):
            self.status.set_message("Select a case in the Corpus tab first", is_error=True)
            return None
        return self.corpus.cases[index]

    def record_expected_tree(self) -> None:
        case = self.selected_case()
        if case is None:
            return
        if case.expect == EXPECT_ERROR:
            self.status.set_message("An error case has no expected tree", is_error=True)
            return
        parser = self.build_parser(announce=True)
        if parser is None:
            return
        try:
            tree = parser.parse(case.text)
        except lark_exceptions.LarkError as exc:
            self.status.set_message(f"Case does not parse: {str(exc).splitlines()[0]}", is_error=True)
            return
        case.tree = tree.pretty().rstrip("\n")
        self.corpus.dirty = True
        self.run_corpus_now()

    def clear_expected_tree(self) -> None:
        case = self.selected_case()
        if case is None:
            return
        case.tree = None
        self.corpus.dirty = True
        self.run_corpus_now()

    def delete_case(self) -> None:
        index = self.result_pane.corpus_view.selected_index()
        if index is None or not 0 <= index < len(self.corpus.cases):
            self.status.set_message("Select a case in the Corpus tab first", is_error=True)
            return
        self.corpus.remove(index)
        self.run_corpus_now()

    def open_corpus_case(self, index: int) -> None:
        if not 0 <= index < len(self.corpus.cases):
            return
        if not self.input_pane.confirm_discard():
            return
        case = self.corpus.cases[index]
        self.input_pane.set_content(case.text)
        self.input_pane.path = None
        self.parse_now()
        self.status.set_message(f"Loaded case '{case.name}' into the input pane")

    def run_corpus_now(self) -> None:
        parser = self.build_parser(announce=False)
        if parser is None:
            self._corpus_summary = ""
            self.refresh_corpus_view("not run, the grammar does not compile")
            self.status.set_detail("")
            return
        passed, total = run_corpus(parser, self.corpus.cases)
        self._show_corpus(self._describe_corpus(passed, total), is_error=passed < total)

    @staticmethod
    def _describe_corpus(passed: int, total: int) -> str:
        if total == 0:
            return "no cases"
        return f"{passed}/{total} passed" if passed < total else f"all {total} passed"

    def refresh_corpus_view(self, summary: str) -> None:
        self.result_pane.corpus_view.show(self.corpus, summary)

    def on_run_corpus_changed(self) -> None:
        self.settings.set("run_corpus_on_parse", self.run_corpus_on_parse.get())
        self.settings.save()
        self.parse_now()

    def _autoload_corpus(self) -> None:
        """Adopt the corpus sitting next to a freshly opened grammar, if there is one."""
        if self.grammar_pane.path is None or self.corpus.dirty:
            return
        candidate = Corpus.default_path_for(self.grammar_pane.path)
        if candidate.is_file() and candidate != self.corpus.path:
            self.load_corpus(candidate, announce=False)

    def clear_parse_tree(self) -> None:
        self.result_pane.clear()
        self.input_pane.clear_error()
        self.status.set_message("Ready")

    def on_grammar_changed(self) -> None:
        if self._highlight_job is not None:
            self.after_cancel(self._highlight_job)
        self._highlight_job = self.after(HIGHLIGHT_DELAY_MS, self._run_highlight)
        self.schedule_parse()

    def _run_highlight(self) -> None:
        self._highlight_job = None
        self.highlighter.highlight()

    def on_view_selected(self) -> None:
        self.result_pane.select_view(self.result_view.get())

    def on_view_changed(self) -> None:
        self.result_view.set(self.result_pane.current_view())
        self.settings.set("result_view", self.result_view.get())

    def on_auto_parse_changed(self) -> None:
        self.settings.set("auto_parse", self.auto_parse.get())
        self.settings.save()
        self.schedule_parse()

    def on_parser_changed(self) -> None:
        self.settings.set("parser", self.parser_name.get())
        self.settings.save()
        self._refresh_parser_badge()
        self.parse_now()

    def ask_start_rule(self) -> None:
        answer = simpledialog.askstring(APP_NAME, "Start rule:", initialvalue=self.start_rule.get(), parent=self)
        if answer and answer.strip():
            self.start_rule.set(answer.strip())
            self.settings.set("start_rule", self.start_rule.get())
            self.settings.save()
            self._refresh_parser_badge()
            self.parse_now()

    def show_about(self) -> None:
        version = lark.__version__ if lark else "not installed"
        messagebox.showinfo(APP_NAME, f"{APP_NAME}\n\nA three pane workbench for Lark grammars.\nlark: {version}")

    def on_quit(self) -> None:
        panes_clear = self.grammar_pane.confirm_discard() and self.input_pane.confirm_discard()
        if not (panes_clear and self.confirm_corpus_discard()):
            return
        self._store_layout()
        self.settings.save()
        self.destroy()

    def destroy(self) -> None:
        """Cancel anything still pending so no callback runs against a dead window."""
        for job in (self._parse_job, self._highlight_job, self._restore_job):
            if job is not None:
                self.after_cancel(job)
        self._parse_job = self._highlight_job = self._restore_job = None
        super().destroy()

    def _store_layout(self) -> None:
        self.settings.set("geometry", self.winfo_geometry())
        self.settings.set("result_view", self.result_pane.current_view())
        try:
            self.settings.set("sashes", [self.panes.sashpos(0), self.panes.sashpos(1)])
        except tk.TclError:
            return  # the window was never mapped, so there is nothing worth storing

    def _restore_sashes(self) -> None:
        self._restore_job = None
        sashes = self.settings.get("sashes", [])
        if len(sashes) != 2 or not all(isinstance(value, int) and value > 0 for value in sashes):
            return
        try:
            for index, position in enumerate(sashes):
                self.panes.sashpos(index, position)
        except tk.TclError:
            return  # a stored position no longer fits this window; the default split stands

    # -- parsing ---------------------------------------------------------------------------------

    def schedule_parse(self) -> None:
        if self._parse_job is not None:
            self.after_cancel(self._parse_job)
            self._parse_job = None
        if self.auto_parse.get():
            self._parse_job = self.after(AUTO_PARSE_DELAY_MS, self.parse_now)

    def _compile_grammar(self) -> tuple[object | None, tuple[str, str] | None]:
        """Compile the grammar pane. Returns (parser, problem); both are None for an empty grammar."""
        if lark is None:
            return None, ("The 'lark' package is not installed.\n\n    pip install lark", "lark is not installed")
        grammar = self.grammar_pane.content()
        if not grammar.strip():
            return None, None
        try:
            parser = lark.Lark(
                grammar,
                parser=self.parser_name.get(),
                start=self.start_rule.get(),
                propagate_positions=True,
                **self._import_options(),
            )
        except lark_exceptions.LarkError as exc:
            return None, (f"Grammar error\n\n{exc}", "Grammar error")
        except OSError as exc:
            return None, (f"Grammar error\n\n{exc}{self._import_hint()}", "Grammar error")
        except Exception as exc:  # noqa: BLE001 - a broken grammar can raise almost anything
            return None, (f"Grammar error\n\n{type(exc).__name__}: {exc}", "Grammar error")
        return parser, None

    def _import_options(self) -> dict:
        """Let %import find grammars sitting next to the one being edited."""
        path = self.grammar_pane.path
        if path is None:
            return {}
        return {"source_path": str(path), "import_paths": [str(path.parent)]}

    def _import_hint(self) -> str:
        if self.grammar_pane.path is not None:
            return ""
        return "\n\nThe grammar has not been saved, so %import has no directory to resolve against."

    def build_parser(self, announce: bool) -> object | None:
        """The compiled grammar, or None. Used by the corpus commands outside a full parse."""
        parser, problem = self._compile_grammar()
        if parser is None and problem is not None and announce:
            self._report_error(*problem)
        return parser

    def parse_now(self) -> None:
        if self._parse_job is not None:
            self.after_cancel(self._parse_job)
            self._parse_job = None
        self.input_pane.clear_error()
        self.input_pane.clear_span()
        self._last_parse_succeeded = False
        self._parsed_source = None

        started = time.perf_counter()
        parser, problem = self._compile_grammar()
        if parser is None:
            self._corpus_summary = ""
            self.status.set_detail("")
            if problem is None:
                self.result_pane.clear()
                self._status("Enter a grammar in the left pane")
            else:
                self._report_error(*problem)
            return

        if self.run_corpus_on_parse.get():
            passed, total = run_corpus(parser, self.corpus.cases)
            self._show_corpus(self._describe_corpus(passed, total), is_error=passed < total)
        else:
            self._corpus_summary = ""
            self.status.set_detail("")

        source = self.input_pane.content()
        try:
            tree = parser.parse(source)
        except lark_exceptions.UnexpectedInput as exc:
            self._report_unexpected_input(exc, source)
            return
        except lark_exceptions.LarkError as exc:
            self._report_error(f"Parse error\n\n{exc}", "Parse error")
            return

        elapsed_ms = (time.perf_counter() - started) * 1000
        node_count = sum(1 for _ in tree.iter_subtrees())
        complete = self.result_pane.show_tree(tree, tree.pretty())
        suffix = "" if complete else f" (tree view truncated at {MAX_TREE_NODES} nodes)"
        self._last_parse_succeeded = True
        self._parsed_source = source
        self._status(f"Parsed OK - {node_count} nodes in {elapsed_ms:.0f} ms{suffix}")

    def _report_unexpected_input(self, exc: Exception, source: str) -> None:
        line = getattr(exc, "line", None)
        column = getattr(exc, "column", None)
        where = f" at line {line}, column {column}" if line and column else ""
        message = str(exc)
        context = exc.get_context(source) if source else ""
        # Some lark exceptions already embed the context in their message; do not repeat it.
        body = message if not context or context.strip() in message else f"{context}\n{message}"
        self._report_error(f"Parse error{where}\n\n{body}", f"Parse error{where}")
        if isinstance(line, int) and isinstance(column, int):
            self.input_pane.highlight_error(line, column)

    def _report_error(self, body: str, status: str) -> None:
        self.result_pane.show_error(body)
        self._status(status, is_error=True)

    def _status(self, message: str, is_error: bool = False) -> None:
        self.status.set_message(message, is_error=is_error)

    def _refresh_parser_badge(self) -> None:
        self.grammar_pane.set_badge(f"parser: {self.parser_name.get()}   start: {self.start_rule.get()}")

    def _show_corpus(self, summary: str, is_error: bool = False) -> None:
        """Put a corpus summary in its tab and in the right of the status line."""
        self._corpus_summary = summary
        self.refresh_corpus_view(summary or "no cases")
        self.status.set_detail(f"corpus: {summary}" if summary else "", is_error=is_error)

    def on_node_selected(self, node: object | None) -> None:
        """Highlight in the input pane the text the selected tree node came from."""
        if node is None:
            self.input_pane.clear_span()
            return
        if self.input_pane.content() != self._parsed_source:
            self.input_pane.clear_span()
            self._status("The input has changed since the last parse, so spans are not shown", is_error=True)
            return
        span = node_span(node)
        if span is None:
            self.input_pane.clear_span()
            return
        self.input_pane.highlight_span(span)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="A three pane tkinter workbench for Lark grammars.")
    parser.add_argument("grammar", nargs="?", type=Path, help="grammar file to load into the left pane")
    parser.add_argument("input", nargs="?", type=Path, help="input file to load into the middle pane")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = LarkIde()
    if args.grammar:
        app.grammar_pane.load_path(args.grammar)
    if args.input:
        app.input_pane.load_path(args.input)
    if args.grammar:
        app.parse_now()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
