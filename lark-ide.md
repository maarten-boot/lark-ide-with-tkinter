# lark-ide

A standalone three pane tkinter workbench for developing [Lark](https://github.com/lark-parser/lark)
grammars, in the spirit of the Lark web IDE.

This document is the specification. It is intended to be unambiguous: anything the application does
should be described here, and anything described here should be implemented in `lark-ide.py`.

## 1. Purpose

Edit a grammar in one pane, edit a sample of the language in a second pane, and see the resulting
parse tree (or the error) in a third. The loop is meant to be tight: type, look, adjust.

## 2. Requirements

- Python 3.10 or later (the code uses `X | None` annotations with `from __future__ import annotations`).
- `tkinter` (on Debian/Ubuntu: `apt install python3-tk`).
- `lark` (`pip install lark`).

If `lark` is missing the application still starts; the right pane and the status line say so, and no
parsing is attempted.

## 3. Invocation

```
lark-ide.py [grammar] [input]
```

Both positional arguments are optional. `grammar` is loaded into the left pane, `input` into the
middle pane. When a grammar is given, an initial parse runs at startup.

## 4. Window layout

```
+--------------------------------------------------------------------------+
| File   Edit   Parse   Help                                     (menu bar) |
+------------------+------------------+------------------------------------+
| Grammar - x.lark | Input - y.txt *  | Parse tree                          |
|                  |                  | [ Text ][ Tree ]           (tabs)   |
| +--------------+ | +--------------+ | +--------------------------------+  |
| |              | | |              | | |                                |  |
| | editable     | | | editable     | | | read only                      |  |
| | text, both   | | | text, both   | | | text, both scrollbars          |  |
| | scrollbars   | | | scrollbars   | | |                                |  |
| +--------------+ | +--------------+ | +--------------------------------+  |
+------------------+------------------+------------------------------------+
| Parsed OK - 12 nodes in 3 ms          parser: earley   start: start       |
+--------------------------------------------------------------------------+
```

- The three columns live in a horizontal `ttk.PanedWindow`; the dividers are draggable and each
  column starts with equal weight.
- Every column has a header label above the text, a vertical scrollbar on the right and a horizontal
  scrollbar below. Word wrap is off in all three panes.
- All three text widgets use `TkFixedFont`.
- Initial window size 1400x850, minimum 900x500.

### 4.1 Pane headers

The header reads `<Title> - <filename>`, where the filename is `(unsaved)` when the pane has no file.
An unsaved change appends ` *`. The right pane header is the fixed text `Parse tree`.

### 4.2 Status line

One line at the bottom, separated from the panes by a horizontal rule.

- Left: the outcome of the last action. Green for success, red for failure.
  - `Ready`
  - `Enter a grammar in the left pane`
  - `Parsed OK - <n> nodes in <ms> ms`
  - `Grammar error`
  - `Parse error at line <l>, column <c>`
  - `Parse tree copied to the clipboard`
- Right: the current parse settings, `parser: <earley|lalr>   start: <rule>`.

## 5. Panes

### 5.1 Left - Grammar

Editable. Undo/redo enabled. Holds Lark grammar text. Default file extension `.lark`; the file
dialog offers `*.lark` and `*.*`.

The grammar is syntax highlighted. Colouring is recomputed over the whole pane 150 ms after the last
keystroke, and immediately after a file is loaded. The token classes follow
`lark.load_grammar.TERMINALS`:

| Class       | Matches                                       | Colour    |
|-------------|-----------------------------------------------|-----------|
| `comment`   | `// ...` and `# ...` to end of line           | `#6a737d` |
| `directive` | `%import %ignore %declare %override %extend`  | `#a626a4` |
| `string`    | `"..."` with an optional `i` suffix           | `#50a14f` |
| `regexp`    | `/.../` with optional `imslux` flags          | `#c18401` |
| `terminal`  | `_?[A-Z][_A-Z0-9]*`                           | `#986801` |
| `rule`      | `_?[a-z][_a-z0-9]*`                           | `#4078f2` |
| `number`    | `[+-]?\d+`, as used by rule priorities         | `#986801` |
| `operator`  | `-> : | ~ ? * + ( ) [ ] { }`                  | `#e45649` |

Alternatives are tried in that order, so a `/regexp/` inside a comment stays comment-coloured. The
highlighter is a lexical approximation for colouring only; it is not the grammar parser, and it is
never consulted when deciding whether a grammar is valid.

### 5.2 Middle - Input

Editable. Undo/redo enabled. Holds the text to be parsed. Default file extension `.txt`; the file
dialog offers `*.txt` and `*.*`.

When a parse fails at a known position, the offending character is given a pink background and is
scrolled into view. The highlight is cleared at the start of every parse.

### 5.3 Right - Parse tree

Two views of the same result, in a `ttk.Notebook` with tabs **Text** and **Tree**. Both are filled
on every successful parse, so switching tabs never triggers a reparse.

#### 5.3.1 Text view

`Tree.pretty()` output, or an error report in red. Read only, and enforced as such: the widget is
kept in Tk's `disabled` state and is only re-enabled internally while its content is being replaced.
Keystrokes cannot alter it.

#### 5.3.2 Tree view

A `ttk.Treeview` with three columns:

| Column     | Rule node               | Token leaf                       |
|------------|-------------------------|-----------------------------------|
| `Node`     | the rule name           | the terminal name                 |
| `Value`    | empty                   | the matched text                  |
| `Line:Col` | from `meta`, when known | from the token                    |

Token values longer than 80 characters are cut to 80 with a trailing `...`; tabs and newlines are
shown escaped. All nodes are expanded when the tree is filled.

To keep a runaway grammar from freezing the window, the tree view stops after 20000 nodes and adds a
final `... truncated at 20000 nodes` row; the status line says so too. The text view is never
truncated.

#### 5.3.3 Selecting and copying

- Mouse drag selects in the text view; a single click selects a row in the tree view.
- `Ctrl+C` copies. In the text view that is the selection, or the whole pane when nothing is
  selected. In the tree view it is `pretty()` of the selected subtree, or the whole tree when
  nothing is selected.
- `Ctrl+A` selects all, in the text view only.
- Right click (and middle click) opens a context menu with **Copy** and **Select All**.

## 6. Menus and keyboard shortcuts

| Menu  | Item                  | Shortcut       | Effect                                              |
|-------|-----------------------|----------------|-----------------------------------------------------|
| File  | New Grammar           |                | Clears the left pane after the unsaved-changes check |
| File  | Open Grammar...       | `Ctrl+O`       | Loads a file into the left pane, then parses         |
| File  | Recent Grammars >     |                | Up to 20 remembered grammars, plus **Clear List**    |
| File  | Save Grammar          | `Ctrl+S`       | Saves the left pane; falls back to Save As if new    |
| File  | Save Grammar As...    |                | Saves the left pane under a chosen name              |
| File  | New Input             |                | Clears the middle pane after the check               |
| File  | Open Input...         | `Ctrl+Shift+O` | Loads a file into the middle pane, then parses       |
| File  | Recent Inputs >       |                | Up to 20 remembered inputs, plus **Clear List**      |
| File  | Save Input            | `Ctrl+Shift+S` | Saves the middle pane                                |
| File  | Save Input As...      |                | Saves the middle pane under a chosen name            |
| File  | Quit                  | `Ctrl+Q`       | Unsaved-changes check on both panes, then exits      |
| Edit  | Copy Parse Tree       |                | Copies the right pane to the clipboard               |
| Edit  | Clear Parse Tree      |                | Empties the right pane, clears the error highlight   |
| Parse | Parse Now             | `F5`           | Parses immediately                                   |
| Parse | Parse While Typing    |                | Checkbutton, on by default                           |
| Parse | Parser: earley        |                | Radio, default                                       |
| Parse | Parser: lalr          |                | Radio                                                |
| Parse | Set Start Rule...     |                | Prompts for the start rule, default `start`          |
| View  | Text View             |                | Radio, selects the Text tab                          |
| View  | Tree View             |                | Radio, selects the Tree tab                          |
| View  | Expand All            |                | Opens every node in the tree view                    |
| View  | Collapse All          |                | Closes every node in the tree view                   |
| Help  | About                 |                | Application name and the installed `lark` version    |

Files are read and written as UTF-8. Read and write failures are reported in a message box; the pane
keeps its contents.

## 7. Unsaved changes

A pane is dirty once its text has been modified since the last load or save. Before an action that
would discard a dirty pane (New, Open, Quit), a Yes/No/Cancel box asks whether to save first.
**Yes** saves and continues, **No** discards and continues, **Cancel** abandons the action.

## 8. Parsing

### 8.1 When it runs

- On `F5` or **Parse Now**, always.
- After loading a grammar or input file.
- After changing the parser or the start rule.
- While typing, if **Parse While Typing** is on: 400 ms after the last keystroke in either editable
  pane. A pending parse is cancelled and rescheduled by each further keystroke, and is cancelled by
  an explicit parse.

### 8.2 What it does

The grammar is compiled with `lark.Lark(grammar, parser=<parser>, start=<start rule>,
propagate_positions=True)`, then the middle pane's text is parsed with it. Both the grammar and the
input are re-read from the panes on every parse; nothing is cached.

An empty or whitespace-only grammar is not an error: the right pane is emptied and the status line
says `Enter a grammar in the left pane`.

### 8.3 Results

On success the right pane shows `Tree.pretty()`, and the status line reports the number of subtree
nodes and the elapsed wall time for compile plus parse.

On a grammar failure the right pane shows `Grammar error` and the exception text.

On an input failure the right pane shows `Parse error at line <l>, column <c>`, the source context
from `UnexpectedInput.get_context()` and the exception text. The context is omitted when the
exception message already contains it, so it never appears twice. The failing character is
highlighted in the middle pane.

## 9. Settings and recent files

Settings live in `~/.lark-ide/settings.json`, written as indented JSON with sorted keys.

| Key               | Meaning                                             | Default    |
|-------------------|-----------------------------------------------------|------------|
| `geometry`        | Window geometry string from the last quit           | `1400x850` |
| `sashes`          | The two pane divider positions, in pixels           | even split |
| `parser`          | `earley` or `lalr`                                  | `earley`   |
| `start_rule`      | The start rule name                                 | `start`    |
| `auto_parse`      | Parse While Typing                                  | `true`     |
| `result_view`     | `text` or `tree`                                    | `text`     |
| `recent_grammar`  | Up to 20 absolute paths, most recent first          | `[]`       |
| `recent_input`    | Up to 20 absolute paths, most recent first          | `[]`       |

Rules that keep the behaviour predictable:

- Paths are stored absolute and resolved. Re-opening a file moves it to the front rather than
  duplicating it.
- A remembered path whose file no longer exists is filtered out when the list is read, so it never
  appears in the menu and is dropped on the next save.
- Settings are written whenever one of them changes (opening or saving a file, switching parser,
  toggling Parse While Typing, changing the start rule) and again on quit. A crash therefore loses
  at most the window layout.
- A settings file that is missing, unreadable or not valid JSON is treated as empty; a value of the
  wrong type falls back to its default. The application never reports a settings problem to the
  user, because nothing about the current session depends on one.
- Layout restore is best effort. Sash positions are reapplied 120 ms after startup and are skipped
  if they no longer fit the window.

## 10. Structure of `lark-ide.py`

| Object        | Responsibility                                                              |
|---------------|-----------------------------------------------------------------------------|
| `Settings`    | Loading, saving and validating `settings.json`, including the recent lists   |
| `GrammarHighlighter` | Regex based colouring of the grammar pane                            |
| `EditorPane`  | Header, editable scrolled text, dirty tracking, open/save/new, error marking |
| `ResultPane`  | The Text/Tree notebook, tree building, copy/select-all, context menu         |
| `StatusBar`   | The two labels of the bottom line                                            |
| `LarkIde`     | `tk.Tk` subclass: menus, layout, key bindings, parse scheduling and reporting |
| `parse_args`  | Command line                                                                 |
| `main`        | Builds the window, applies the command line, runs the main loop               |

## 11. Code style

4-space indent, 120-character lines, formatted and checked with
`ruff format --line-length 120` and `ruff check --line-length 120`.

`test_lark_ide.py` drives a real window and covers parsing, both result views, the read-only
guarantee, clipboard behaviour, highlighting, file round-trips and settings persistence. It needs a
display:

```
HOME=$(mktemp -d) xvfb-run -a python3 test_lark_ide.py
```

`HOME` is overridden so a test run cannot touch a real `~/.lark-ide/`.

## 12. Not in this version

Deliberately left out for now, listed here so the spec stays honest about its boundaries:

- A saved corpus of test inputs re-run on every grammar edit (agreed as the next round).
- Terminal/token view, `ambiguity='explicit'` forest display, railroad diagrams.
- Clicking a tree node to select the matching span in the input.
- Grammar `%import` resolution relative to the grammar file's directory.
