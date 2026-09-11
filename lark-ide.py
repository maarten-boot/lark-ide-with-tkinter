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
MAX_TREE_NODES = 20000
MAX_TREE_VALUE_CHARS = 80

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

        self.header = ttk.Label(self, anchor="w", padding=(4, 3))
        self.header.pack(fill="x")

        self.text = ScrolledText(self, wrap="none", undo=True, font=editor_font, width=40, height=25)
        self.text.pack(fill="both", expand=True)

        hbar = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        hbar.pack(fill="x")
        self.text.configure(xscrollcommand=hbar.set)
        self.text.tag_configure(ERROR_TAG, background=HIGHLIGHT_COLOUR)

        self.text.bind("<<Modified>>", self._on_modified)
        self._refresh_header()

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

    def clear_error(self) -> None:
        self.text.tag_remove(ERROR_TAG, "1.0", "end")

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


class ResultPane(ttk.Frame):
    """The right hand pane: the parse result as pretty text and as a collapsible tree."""

    def __init__(
        self, master: tk.Misc, title: str, editor_font: tkfont.Font, on_view_change: Callable[[], None]
    ) -> None:
        super().__init__(master)
        self.base_title = title
        self.on_view_change = on_view_change
        self.pretty = ""
        self.nodes: dict[str, object] = {}

        self.header = ttk.Label(self, anchor="w", padding=(4, 3), text=title)
        self.header.pack(fill="x")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.add(self._build_text_view(editor_font), text="Text")
        self.notebook.add(self._build_tree_view(), text="Tree")
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
        self.tree.bind("<Button-3>", self._on_context_menu)
        self.tree.bind("<Button-2>", self._on_context_menu)
        return frame

    # -- views -----------------------------------------------------------------------------------

    def current_view(self) -> str:
        return TREE_VIEW if self.notebook.index("current") == 1 else TEXT_VIEW

    def select_view(self, view: str) -> None:
        self.notebook.select(1 if view == TREE_VIEW else 0)

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

    def set_detail(self, text: str) -> None:
        self.detail.configure(text=text)


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
        self._parse_job: str | None = None
        self._highlight_job: str | None = None
        self._restore_job: str | None = None

        self._build_body()
        self._build_menu()
        self._build_bindings()
        self.protocol("WM_DELETE_WINDOW", self.on_quit)
        self.result_pane.select_view(self.result_view.get())
        self._restore_job = self.after(120, self._restore_sashes)
        self._refresh_detail()
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
        self.result_pane = ResultPane(self.panes, "Parse tree", self.editor_font, self.on_view_changed)
        self.highlighter = GrammarHighlighter(self.grammar_pane.text)

        for pane in (self.grammar_pane, self.input_pane, self.result_pane):
            self.panes.add(pane, weight=1)

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

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_radiobutton(
            label="Text View", value=TEXT_VIEW, variable=self.result_view, command=self.on_view_selected
        )
        view_menu.add_radiobutton(
            label="Tree View", value=TREE_VIEW, variable=self.result_view, command=self.on_view_selected
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
        self._refresh_detail()
        self.parse_now()

    def ask_start_rule(self) -> None:
        answer = simpledialog.askstring(APP_NAME, "Start rule:", initialvalue=self.start_rule.get(), parent=self)
        if answer and answer.strip():
            self.start_rule.set(answer.strip())
            self.settings.set("start_rule", self.start_rule.get())
            self.settings.save()
            self._refresh_detail()
            self.parse_now()

    def show_about(self) -> None:
        version = lark.__version__ if lark else "not installed"
        messagebox.showinfo(APP_NAME, f"{APP_NAME}\n\nA three pane workbench for Lark grammars.\nlark: {version}")

    def on_quit(self) -> None:
        if not (self.grammar_pane.confirm_discard() and self.input_pane.confirm_discard()):
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

    def parse_now(self) -> None:
        if self._parse_job is not None:
            self.after_cancel(self._parse_job)
            self._parse_job = None
        self.input_pane.clear_error()

        if lark is None:
            self._report_error("The 'lark' package is not installed.\n\n    pip install lark", "lark is not installed")
            return

        grammar = self.grammar_pane.content()
        if not grammar.strip():
            self.result_pane.clear()
            self.status.set_message("Enter a grammar in the left pane")
            return

        started = time.perf_counter()
        try:
            parser = lark.Lark(
                grammar,
                parser=self.parser_name.get(),
                start=self.start_rule.get(),
                propagate_positions=True,
            )
        except lark_exceptions.LarkError as exc:
            self._report_error(f"Grammar error\n\n{exc}", "Grammar error")
            return
        except Exception as exc:  # noqa: BLE001 - a broken grammar can raise almost anything
            self._report_error(f"Grammar error\n\n{type(exc).__name__}: {exc}", "Grammar error")
            return

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
        self.status.set_message(f"Parsed OK - {node_count} nodes in {elapsed_ms:.0f} ms{suffix}")

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
        self.status.set_message(status, is_error=True)

    def _refresh_detail(self) -> None:
        self.status.set_detail(f"parser: {self.parser_name.get()}   start: {self.start_rule.get()}")


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
