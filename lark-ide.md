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
| Grammar - x.lark |                  |                                     |
|   parser: earley | Input - y.txt *  | Parse tree                          |
|                  |                  | [Text][Tree][Tokens][Corpus] (tabs) |
| +--------------+ | +--------------+ | +--------------------------------+  |
| |              | | |              | | |                                |  |
| | editable     | | | editable     | | | read only                      |  |
| | text, both   | | | text, both   | | | text, both scrollbars          |  |
| | scrollbars   | | | scrollbars   | | |                                |  |
| +--------------+ | +--------------+ | +--------------------------------+  |
+------------------+------------------+------------------------------------+
| Parsed OK - 12 nodes in 3 ms                        corpus: 3/4 passed    |
+--------------------------------------------------------------------------+
```

- The three columns live in a horizontal `ttk.PanedWindow`; the dividers are draggable and start at
  three equal columns, or wherever they were left last time (section 11).
- Every column has a header label above the text, a vertical scrollbar on the right and a horizontal
  scrollbar below. Word wrap is off in all three panes.
- All three text widgets use `TkFixedFont`.
- Initial window size 1400x850, minimum 900x500.

### 4.1 Pane headers

The header reads `<Title> - <filename>`, where the filename is `(unsaved)` when the pane has no file.
An unsaved change appends ` *`. The right pane header is the fixed text `Parse tree`.

The grammar pane's header also carries a right-aligned badge with the settings the next parse will
use, `parser: earley   start: start`. It sits above the grammar because that is what those settings
apply to, and it updates the moment the parser or start rule changes.

### 4.2 Status line

One line at the bottom, separated from the panes by a horizontal rule.

- Left: the outcome of the last action. Green for success, red for failure.
  - `Ready`
  - `Enter a grammar in the left pane`
  - `Parsed OK - <n> nodes in <ms> ms`
  - `Grammar error`
  - `Parse error at line <l>, column <c>`
  - `Parse tree copied to the clipboard`
- Right: the corpus result, `corpus: all 4 passed` or `corpus: 3/4 passed`, red when any case
  failed. Empty when the corpus did not run for this parse, so it never shows a stale figure. The
  parse settings are not repeated here; they live above the grammar pane (section 4.1).

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

Selecting a node in the tree view gives the text that node came from a blue background and scrolls
it into view (section 5.3.2). The two marks are independent tags; the error mark draws on top.

### 5.3 Right - Parse tree

A `ttk.Notebook` with tabs **Text**, **Tree**, **Tokens** and **Corpus**. Text and Tree are two
views of the same parse result and are both filled on every successful parse, so switching tabs
never triggers a reparse. Corpus is described in section 9.

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

Selecting a row highlights the matching text in the input pane, using `meta` for a rule node and the
token's own position for a leaf. Nodes that carry no position, and rules whose children were all
filtered out, highlight nothing.

The highlight is refused outright if the input has been edited since the parse that produced the
tree: the positions would point at the wrong text, so the status line says so instead. Clearing the
selection, or any new parse, clears the highlight.

#### 5.3.2.1 The other direction: input to node

Moving the caret or making a selection in the input pane selects the matching node in the tree view
and scrolls it into view. The node chosen is the **deepest** one whose span covers the caret, or
covers the whole selection when there is one, so clicking inside a token lands on the token rather
than on `start`.

- **View > Follow Cursor in Tree** (on by default) does this on every click and key release.
- **View > Find Node at Cursor** (`F7`) does it on demand and switches to the Tree tab first. With
  following switched off, this is the only way in.

The two directions agree: selecting a node marks its text, and putting the caret in that text
selects the node again. Following does not fight with the span highlight, because the highlight is
its own tag and never moves the caret.

As with the forward direction, a tree built from text that has since been edited is not searched.
Following simply does nothing; `F7` says why.

To keep a runaway grammar from freezing the window, the tree view stops after 20000 nodes and adds a
final `... truncated at 20000 nodes` row; the status line says so too. The text view is never
truncated.

#### 5.3.3 Tokens

What the lexer produced, before the parser saw it. Many apparent grammar bugs are terminal bugs: two
terminals matching the same text with priority deciding the winner, `%ignore` swallowing something
needed, a regex greedier than intended. The parse tree cannot show any of that, because by then the
decision has been made.

Two tables in a vertical split:

- **The token stream**: Token, Value, Line:Col, in order. Ignored tokens are listed too, greyed, so
  you can see what `%ignore` consumed. Selecting a row highlights that token in the input pane.
- **The terminals**: Terminal, Priority, Uses, Pattern, sorted by name. Pattern is the source form
  where lark kept one, otherwise the compiled regex. A terminal with no uses in the current input is
  greyed, which is how you spot a terminal that never fires.

Lexing runs separately from parsing, on every parse that compiles the grammar, so the tokens are
there **even when the input does not parse** — that is the case the view exists for. If lexing
itself fails, the tokens read up to that point are kept and the header says where it stopped.

The stream is capped at 5000 tokens, and the header says so when it truncates.

#### 5.3.4 Selecting and copying

- Mouse drag selects in the text view; a single click selects a row in the tree view.
- `Ctrl+C` copies. In the text view that is the selection, or the whole pane when nothing is
  selected. In the tree view it is `pretty()` of the selected subtree, or the whole tree when
  nothing is selected.
- `Ctrl+A` selects all, in the text view only.
- Right click (and middle click) opens a context menu with **Copy** and **Select All**.

### 5.4 Context menus

Right click (and middle click) in the grammar or input pane opens a menu with that pane's file
commands at the top, then **Cut**, **Copy**, **Paste**, **Select All**, **Undo**, **Redo**. The file
commands are the same ones the File menu offers for that pane, so they keep the recent-files list up
to date; the input pane's menu also carries **Add Input as Case...**.

The file commands are installed by the application after the panes are built, and replace any set
before, so a pane never accumulates stale entries.

## 6. Menus and keyboard shortcuts

| Menu  | Item                  | Shortcut       | Effect                                              |
|-------|-----------------------|----------------|-----------------------------------------------------|
| File  | New Grammar           |                | Clears the left pane after the unsaved-changes check |
| File  | Open Grammar...       | `Ctrl+O`       | Loads a file into the left pane, then parses         |
| File  | Recent Grammars >     |                | Up to 20 remembered grammars, plus **Clear List**    |
| File  | Save Grammar          | `Ctrl+S`       | Saves the left pane; falls back to Save As if new    |
| File  | Save Grammar As...    |                | Saves the left pane under a chosen name              |
| File  | Export Railroad SVG...|                | Writes the grammar as diagrams (section 10)          |
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
| Parse | Show Ambiguity        |                | Checkbutton, off by default, earley only             |
| Parse | Watch Imported Files  |                | Checkbutton, on by default                            |
| Corpus| New Corpus            |                | Empties the corpus after the unsaved-changes check   |
| Corpus| Open Corpus...        |                | Loads a corpus file and runs it                      |
| Corpus| Save Corpus           |                | Saves the corpus; falls back to Save As if new       |
| Corpus| Save Corpus As...     |                | Defaults to the name beside the current grammar      |
| Corpus| Add Input as Case...  |                | Adds the input pane as a case expected to parse      |
| Corpus| Add Input as Error Case... |           | Adds it as a case expected to fail                   |
| Corpus| Record Expected Tree  |                | Stores the selected case's current tree              |
| Corpus| Clear Expected Tree   |                | Drops it again, leaving a parses-or-not case         |
| Corpus| Set Case Parser...    |                | Pins this case's parser; blank clears it             |
| Corpus| Set Case Start Rule...|                | Pins this case's start rule; blank clears it         |
| Corpus| Delete Case           |                | Removes the selected case                            |
| Corpus| Run Corpus            | `F6`           | Runs every case now                                  |
| Corpus| Run On Every Parse    |                | Checkbutton, on by default                           |
| View  | Text View             |                | Radio, selects the Text tab                          |
| View  | Tree View             |                | Radio, selects the Tree tab                          |
| View  | Tokens View           |                | Radio, selects the Tokens tab                        |
| View  | Corpus View           |                | Radio, selects the Corpus tab                        |
| View  | Follow Cursor in Tree |                | Checkbutton, on by default                            |
| View  | Find Node at Cursor   | `F7`           | Selects the node covering the caret, showing the tree|
| View  | Reset Layout          |                | Three equal columns again                            |
| View  | Expand All            |                | Opens every node in the tree view                    |
| View  | Collapse All          |                | Closes every node in the tree view                   |
| Help  | About                 |                | Application name and the installed `lark` version    |

Files are read and written as UTF-8. Read and write failures are reported in a message box; the pane
keeps its contents.

## 7. Unsaved changes

A pane is dirty once its text has been modified since the last load or save. Before an action that
would discard a dirty pane (New, Open, Quit), a Yes/No/Cancel box asks whether to save first.
**Yes** saves and continues, **No** discards and continues, **Cancel** abandons the action.

The corpus is dirty once a case has been added, deleted, or had its expected tree recorded or
cleared. It gets the same prompt before New Corpus, Open Corpus and Quit.

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

#### 8.2.1 Resolving `%import`

When the grammar pane has a file, two more options are passed:

- `source_path` is the grammar's own path, which is what `%import .module.rule` resolves against.
- `import_paths` is the grammar's directory, which is what plain `%import module.rule` searches.

So a grammar split across several files works as long as the pieces sit together, and two projects
can each define their own `shared.lark` without colliding: the grammar currently loaded decides
which one is found. Lark's bundled `common` grammars keep working either way.

An unsaved grammar has no directory to resolve against, so it gets neither option. A `%import` of a
local file then fails with a file-not-found error, and the report adds a line saying the grammar has
not been saved, because the raw error does not make the cause obvious.

#### 8.2.2 Ambiguity

Earley resolves an ambiguous grammar silently, by rule priority, and hands back one tree. You never
learn the grammar was undecided. With **Parse > Show Ambiguity (earley only)** the parser is built
with `ambiguity='explicit'`, and every point where more than one derivation was possible comes back
as an `_ambig` node whose children are the alternatives.

In the tree view such a node reads `_ambig (2 derivations)` in orange, and its children are
labelled `derivation 1: ...`, `derivation 2: ...`. The status line appends `2 ambiguous nodes` to
the parse result, so an ambiguity is visible without opening the tree.

This is the readable form of what LALR tells you as a shift/reduce conflict. The option is off by
default, because most of the time you do not want the extra nodes.

`lalr` rejects `ambiguity='explicit'` outright, so the option is simply not passed when lalr is
selected. Switching it on while lalr is active says so in the status line rather than failing the
parse.

#### 8.2.3 Watching imported files

An imported grammar is read once, when the grammar is compiled. Editing it in another editor would
otherwise have no effect until the main grammar was touched, which is confusing while splitting a
grammar across files.

With **Parse > Watch Imported Files** on (the default), the imported files are polled once a second
and any change triggers a reparse, with `Reparsed: shared.lark changed on disk` in the status line.

The timer body and the check are separate methods, `_watch_tick` and `check_imports`. Calling the
check directly is therefore safe: it does not touch the pending timer, so the timer can still be
cancelled when the window closes.

- The list comes from `lark.load_grammar.list_grammar_imports`, so it is what lark actually resolved,
  recursively, not a guess from reading `%import` lines.
- Only real paths are watched. Lark's bundled grammars come back as package resources, not files,
  and are skipped.
- The list is rebuilt whenever the grammar text or its path changes, and only then, so a per-keystroke
  parse does not pay for it. A grammar that does not compile keeps the previous list, since there is
  nothing better to know.
- A file that disappears counts as a change, so deleting an import reports itself rather than going
  quiet.
- Polling, not inotify: up to a second of lag, no platform-specific dependency, and nothing to leak
  if the application exits badly.

### 8.3 Results

On success the right pane shows `Tree.pretty()`, and the status line reports the number of subtree
nodes and the elapsed wall time for compile plus parse.

On a grammar failure the right pane shows `Grammar error` and the exception text.

On an input failure the right pane shows `Parse error at line <l>, column <c>`, the source context
from `UnexpectedInput.get_context()` and the exception text. The context is omitted when the
exception message already contains it, so it never appears twice. The failing character is
highlighted in the middle pane.

## 9. The corpus

A corpus is a named set of sample inputs for one grammar, kept in a JSON file beside it. It exists
to answer the question a single input pane cannot: *did this grammar edit break anything that used
to work?*

### 9.1 File format

```json
{
  "version": 1,
  "cases": [
    {
      "name": "simple pair",
      "input": "a = 1\n",
      "expect": "parse",
      "tree": "start\n  pair\n    a\n    value\t1"
    },
    { "name": "missing value", "input": "a =\n", "expect": "error" },
    { "name": "one pair", "input": "a = 1", "expect": "parse", "start": "pair", "parser": "lalr" }
  ]
}
```

- `name` is free text and is made unique on entry by appending ` (2)`, ` (3)` and so on.
- `input` is the exact text to parse.
- `expect` is `parse` or `error`. Anything else is read as `parse`.
- `tree` is optional and only meaningful for `expect: parse`. When present it is the recorded
  `pretty()` output, compared exactly after trailing newlines are stripped.
- `parser` and `start` are optional overrides, described in section 9.7. Absent means "use whatever
  the application is set to". An unrecognised `parser`, or a blank `start`, reads as absent.

Only those keys are written, and only when set. A case's last result is deliberately not persisted: it describes a
run, not the case.

A file that is missing, unreadable, not valid JSON, or has no `cases` list is reported as an error
when the user asked for it by name, and ignored silently when it was found automatically.

### 9.2 Where it lives

The default path for a grammar is the grammar path with its suffix replaced by `.corpus.json`, so
`demo.lark` pairs with `demo.corpus.json`. Opening a grammar adopts that file if it exists and the
current corpus has no unsaved changes; if there is no such file the current corpus is left alone,
because switching grammars to compare them is a normal thing to do.

### 9.3 What a case proves

| `expect` | `tree`   | The case passes when                                    |
|----------|----------|---------------------------------------------------------|
| `parse`  | absent   | the input parses                                        |
| `parse`  | present  | the input parses **and** produces exactly that tree     |
| `error`  | ignored  | the input fails to parse                                |

The recorded tree is the point of the feature. A grammar edit that renames a rule, reassociates an
operator or changes where a token lands still parses the same inputs; only the recorded tree
notices. The trade-off is that recorded trees go stale on purpose: after an intended grammar change
the affected cases fail until their trees are re-recorded.

### 9.4 When it runs

With **Run On Every Parse** on, the corpus runs on every parse that compiles the grammar, against
the same compiled parser, before the input pane is parsed. It therefore runs even when the input
pane itself does not parse. `F6` runs it on demand regardless of the setting.

If the grammar does not compile, no case is run and the tab says so rather than reporting failures
that are really one grammar error.

### 9.5 Per-case parser and start rule

A case can pin the parser or the start rule it runs under, through **Corpus > Set Case Parser...**
and **Corpus > Set Case Start Rule...**. Leaving either dialog blank clears the override.

Two things this makes possible:

- **Fragments.** `a = 1` is not a whole document but it is a valid `pair`. A case with
  `start: pair` tests that rule directly, without wrapping it in enough context to satisfy `start`.
- **Parser-specific expectations.** A case can assert that an input parses under `earley` while
  another asserts the grammar is still LALR-clean, in the same run.

The parser is compiled once per distinct (parser, start rule) combination in the run and reused, so
a corpus of fifty cases sharing three combinations compiles three parsers, not fifty.

If a combination does not compile — usually a start rule that is not a rule — only the cases asking
for it fail, with `grammar does not compile with parser=lalr start=nosuchrule` in Detail. The rest
of the corpus still runs.

### 9.6 The Corpus tab

A row per case: **Case**, **Expect**, **Parser**, **Start**, **Result**, **Detail**. Parser and
Start are blank for a case that inherits the current settings. Passing rows are green, failing rows
red. `Detail` carries the first line of the parse error, or `tree differs from the recorded one`, or
`parsed, but an error was expected`.

Above the rows sits the corpus file name, a `*` when there are unsaved changes, and a summary:
`all 4 passed`, `3/4 passed`, `no cases`, or `not run, the grammar does not compile`.

Double clicking a row loads that case's text into the input pane, after the usual unsaved-changes
check, and parses it. The input pane then has no file attached, so saving it will ask for a name
rather than overwriting whatever was loaded before.

Selecting a row is required by Record Expected Tree, Clear Expected Tree and Delete Case; without a
selection they say so in the status line and do nothing.

### 9.7 In the status line

When the corpus has run, its summary appears on the right of the status line, separately from the
parse outcome on the left:

```
Parsed OK - 12 nodes in 3 ms                        corpus: 3/4 passed
```

It is red when any case failed. It is cleared as soon as a parse runs without the corpus, so it
never shows a stale result.

## 10. Railroad diagrams

**File > Export Railroad SVG...** writes the whole grammar as one SVG document: a diagram per rule
and per terminal, in source order, with the name above each.

This is documentation output, not a view. Nothing is drawn inside the application, the diagrams do
not follow edits, and there is no viewer: the file is meant to be opened in a browser, printed, or
dropped into teaching material.

### 10.1 How it works

`lark_railroad.py` is a standalone module with no dependency on the application or on tkinter, so it
can be used on its own:

```python
from lark_railroad import grammar_svg
open("grammar.svg", "w").write(grammar_svg(open("grammar.lark").read()))
```

The grammar is parsed with lark's own `lark.lark` grammar rather than by reading the text, so the
structure is whatever lark says it is. That tree is mapped onto five shapes — `Leaf`, `Skip`,
`Sequence`, `Choice`, `Repeat` — each of which reports a width and how far it reaches above and
below the line running through it. Sizes are gathered bottom up, positions handed down, and the
result is emitted as SVG paths.

| Grammar          | Diagram                                          |
|------------------|--------------------------------------------------|
| `a b c`          | a sequence, left to right                        |
| `a \| b \| c`     | a choice: first alternative on the line, rest below |
| `a?`, `[a]`      | a choice between `a` and an empty path           |
| `a+`             | `a` with a loop back underneath                  |
| `a*`             | the same loop, with an empty bypass              |
| `a~2..4`         | a loop labelled `2 to 4`                         |
| rule reference   | a blue rectangle                                 |
| terminal or literal | a green rounded box                           |

Rules and terminals are drawn differently on purpose: a reader needs to tell "go and look at another
rule" from "match this text".

### 10.2 Limits

- Aliases (`-> name`) do not change the shape and are not drawn.
- Template usage renders as a single box, `tmpl{...}`, rather than being expanded.
- Terminal regexes are drawn as one box containing the pattern, not diagrammed themselves.
- Labels longer than 40 characters are cut with an ellipsis.
- A grammar that does not parse cannot be drawn, and the error says so. Note that this is lark's own
  grammar parser, so a grammar can be drawable while still being a broken grammar — an undefined
  rule reference draws fine.

## 11. Settings and recent files

Settings live in `~/.lark-ide/settings.json`, written as indented JSON with sorted keys.

| Key               | Meaning                                             | Default    |
|-------------------|-----------------------------------------------------|------------|
| `geometry`        | Window geometry string from the last quit           | `1400x850` |
| `sash_fractions`  | The two dividers as proportions of the window width | `[0.33, 0.67]` |
| `parser`          | `earley` or `lalr`                                  | `earley`   |
| `start_rule`      | The start rule name                                 | `start`    |
| `auto_parse`      | Parse While Typing                                  | `true`     |
| `result_view`     | `text`, `tree`, `tokens` or `corpus`                | `text`     |
| `run_corpus_on_parse` | Run On Every Parse                              | `true`     |
| `watch_imports`   | Watch Imported Files                                | `true`     |
| `follow_cursor`   | Follow Cursor in Tree                               | `true`     |
| `show_ambiguity`  | Show Ambiguity                                      | `false`    |
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
- The dividers are stored as proportions of the pane area, not pixels, so a layout saved on a wide
  screen still makes sense on a narrow one. They are reapplied 120 ms after startup, and retried up
  to eight times at that interval until the window manager has actually sized the window: until it
  has, the pane area is one pixel wide and there is nothing sensible to place.
- Stored proportions are validated before use: two values, both between 0 and 1, in increasing
  order. Anything else falls back to three equal columns. On applying them, each column is clamped
  to at least 80 pixels, so no stored layout can leave a column invisible.
- The `sashes` key written by earlier versions held absolute pixel positions and is ignored and
  removed on the next save. Those could hide a column outright when the window was later opened at a
  different size.
- **View > Reset Layout** puts the dividers back to three equal columns, for when a layout has been
  dragged somewhere unhelpful.

## 12. Structure of the code

| Object        | Responsibility                                                              |
|---------------|-----------------------------------------------------------------------------|
| `Settings`    | Loading, saving and validating `settings.json`, including the recent lists   |
| `CorpusCase`  | One case: name, input, expectation, optional recorded tree, last result      |
| `Corpus`      | The case list, its file, dirty tracking, and the default path for a grammar  |
| `run_corpus`  | Runs every case against the parser its supplier returns, recording outcomes  |
| `single_parser` | A supplier that gives every case the same parser, for the simple case      |
| `node_span`   | The line/column range a tree node or token covers, or None                   |
| `CorpusView`  | The Corpus tab                                                              |
| `TokenView`   | The Tokens tab: the token stream and the terminal table                     |
| `GrammarHighlighter` | Regex based colouring of the grammar pane                            |
| `EditorPane`  | Header, editable scrolled text, dirty tracking, open/save/new, error marking |
| `ResultPane`  | The Text/Tree notebook, tree building, copy/select-all, context menu         |
| `StatusBar`   | The two labels of the bottom line                                            |
| `LarkIde`     | `tk.Tk` subclass: menus, layout, key bindings, parse scheduling and reporting |
| `parse_args`  | Command line                                                                 |
| `main`        | Builds the window, applies the command line, runs the main loop               |

`lark_railroad.py` is separate and standalone:

| Object          | Responsibility                                                            |
|-----------------|---------------------------------------------------------------------------|
| `Item` and subclasses | `Leaf`, `Skip`, `Sequence`, `Choice`, `Repeat`: sizing and drawing   |
| `grammar_items` | Parses a grammar with `lark.lark` and maps each rule onto items            |
| `grammar_svg`   | The whole grammar as one SVG document                                     |

## 13. Code style, tests and the Makefile

4-space indent, 120-character lines, formatted and checked with
`ruff format --line-length 120` and `ruff check --line-length 120`.

`test_lark_ide.py` drives a real window and covers parsing, all three result views, the read-only
guarantee, clipboard behaviour, highlighting, context menus, file round-trips, the corpus format and
runner, and settings persistence.

The `Makefile` is the entry point:

| Target         | Does                                                    |
|----------------|---------------------------------------------------------|
| `make run`     | Starts the application                                  |
| `make format`  | `ruff format`                                           |
| `make lint`    | `ruff check`                                            |
| `make fix`     | `ruff check --fix`, then reformat                       |
| `make test`    | Runs the suite                                          |
| `make check`   | Format check, lint and tests: what CI should run        |
| `make deps`    | Installs `lark` and `ruff`, warns if tkinter is missing |
| `make clean`   | Removes caches                                          |

`make test` needs a display. With `DISPLAY` set it uses it; otherwise it falls back to `xvfb-run`,
so the suite works over ssh. It always runs with a throwaway `HOME`, so a test run can never touch a
real `~/.lark-ide/`.

## 14. Not in this version

Deliberately left out for now, listed here so the spec stays honest about its boundaries:

- Stepping through alternative derivations side by side, rather than as nested tree rows.
- Drawing railroad diagrams inside the application; export only.
- Diagramming terminal regexes rather than showing them as a box.
- A recent-corpora list; only grammars and inputs are remembered.
- Re-recording every stale tree in one command after an intended grammar change.
