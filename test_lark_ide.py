#!/usr/bin/env python3
"""Tests for lark-ide.

Needs a display; run headless with:

    HOME=$(mktemp -d) xvfb-run -a python3 test_lark_ide.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from xml.etree import ElementTree

import lark

HERE = Path(__file__).resolve().parent
GRAMMAR = (
    "start: pair+\n"
    'pair: NAME "=" value  // a pair\n'
    "value: NUMBER | ESCAPED_STRING | WORD\n"
    "WORD: /[a-z]+/i\n"
    "%import common.CNAME -> NAME\n"
    "%import common.NUMBER\n"
    "%import common.ESCAPED_STRING\n"
    "%import common.WS\n"
    "%ignore WS\n"
)
INPUT = 'a = 1\nb = "two"\n'


def load_module():
    spec = importlib.util.spec_from_file_location("larkide", HERE / "lark-ide.py")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their annotations through sys.modules, so register before executing
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ide = load_module()
railroad = importlib.import_module("lark_railroad")


class IdeTestCase(unittest.TestCase):
    """Common setup: a real application window backed by a throwaway config directory."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.settings_path = self.tmp / "settings.json"
        self._original_settings_path = ide.SETTINGS_PATH
        ide.SETTINGS_PATH = self.settings_path
        self.windows: list = []
        self.app = self.new_app()

    def tearDown(self) -> None:
        ide.SETTINGS_PATH = self._original_settings_path
        # Every window ever built here, not just self.app: a survivor keeps its timers running
        # and they fire against a destroyed interpreter later, in some unrelated test.
        for window in self.windows:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def new_app(self) -> object:
        app = ide.LarkIde()
        app.update()
        self.windows.append(app)
        return app

    def parse(self, grammar: str = GRAMMAR, text: str = INPUT) -> None:
        self.app.grammar_pane.set_content(grammar)
        self.app.input_pane.set_content(text)
        self.app.parse_now()
        self.app.update()

    def status(self) -> str:
        return self.app.status.message.cget("text")

    def detail(self) -> str:
        return self.app.status.detail.cget("text")

    def badge(self) -> str:
        return self.app.grammar_pane.badge.cget("text")


class TestParsing(IdeTestCase):
    """Behaviour that existed before the settings/tree-view round; re-checked against the rewrite."""

    def test_successful_parse_reports_nodes_and_fills_text_view(self) -> None:
        self.parse()
        self.assertIn("Parsed OK", self.status())
        self.assertIn("pair", self.app.result_pane.text.get("1.0", "end"))

    def test_empty_grammar_is_not_an_error(self) -> None:
        self.parse(grammar="   \n")
        self.assertEqual(self.app.result_pane.text.get("1.0", "end-1c"), "")
        self.assertIn("Enter a grammar", self.status())

    def test_grammar_error_is_reported(self) -> None:
        self.parse(grammar="start: MISSING\n")
        self.assertIn("Grammar error", self.app.result_pane.text.get("1.0", "end"))
        self.assertIn("Grammar error", self.status())

    def test_input_error_highlights_the_offending_character(self) -> None:
        self.parse(text="a = 1\nb = ?\n")
        self.assertIn("line 2, column 5", self.status())
        self.assertTrue(self.app.input_pane.text.tag_ranges(ide.ERROR_TAG))

    def test_error_context_is_not_repeated(self) -> None:
        self.parse(text="a = ?\n")
        body = self.app.result_pane.text.get("1.0", "end")
        self.assertEqual(body.count("a = ?"), 1)

    def test_error_highlight_is_cleared_by_the_next_good_parse(self) -> None:
        self.parse(text="a = ?\n")
        self.parse()
        self.assertFalse(self.app.input_pane.text.tag_ranges(ide.ERROR_TAG))

    def test_corpus_result_sits_in_the_status_detail_not_the_message(self) -> None:
        self.app.grammar_pane.set_content(GRAMMAR)
        self.app.corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        self.parse()
        self.assertIn("Parsed OK", self.status())
        self.assertNotIn("corpus", self.status())
        self.assertEqual(self.detail(), "corpus: all 1 passed")

    def test_lalr_parser_is_usable(self) -> None:
        self.app.parser_name.set("lalr")
        self.app.on_parser_changed()
        self.parse()
        self.assertIn("Parsed OK", self.status())
        self.assertIn("lalr", self.badge())


class TestResultPaneIsReadOnly(IdeTestCase):
    """The right pane must reject edits but still allow selecting and copying."""

    def test_keystrokes_do_not_change_the_text_view(self) -> None:
        self.parse()
        before = self.app.result_pane.text.get("1.0", "end")
        self.app.result_pane.text.event_generate("<Key>", keysym="x")
        self.app.update()
        self.assertEqual(self.app.result_pane.text.get("1.0", "end"), before)

    def test_copy_from_the_text_view(self) -> None:
        self.parse()
        self.app.result_pane.select_view(ide.TEXT_VIEW)
        self.app.result_pane.select_all()
        self.app.result_pane.copy_selection()
        self.app.update()
        self.assertTrue(self.app.clipboard_get().startswith("start"))

    def test_copy_from_the_tree_view_uses_the_selected_subtree(self) -> None:
        self.parse()
        self.app.result_pane.select_view(ide.TREE_VIEW)
        pairs = [i for i in self.app.result_pane._walk() if self.app.result_pane.tree.item(i, "text") == "pair"]
        self.app.result_pane.tree.selection_set(pairs[0])
        self.app.result_pane.copy_selection()
        self.app.update()
        self.assertTrue(self.app.clipboard_get().startswith("pair"))

    def test_copy_from_the_tree_view_falls_back_to_the_whole_tree(self) -> None:
        self.parse()
        self.app.result_pane.select_view(ide.TREE_VIEW)
        self.app.result_pane.tree.selection_set(())
        self.app.result_pane.copy_selection()
        self.app.update()
        self.assertTrue(self.app.clipboard_get().startswith("start"))


class TestTreeView(IdeTestCase):
    def rows(self) -> list[tuple[str, tuple]]:
        pane = self.app.result_pane
        return [(pane.tree.item(i, "text"), pane.tree.item(i, "values")) for i in pane._walk()]

    def test_rules_and_tokens_both_appear(self) -> None:
        self.parse()
        labels = [label for label, _ in self.rows()]
        self.assertIn("start", labels)
        self.assertIn("NAME", labels)

    def test_tokens_carry_value_and_position(self) -> None:
        self.parse()
        names = [values for label, values in self.rows() if label == "NAME"]
        self.assertEqual(names[0][0], "a")
        self.assertEqual(names[0][1], "1:1")

    def test_long_token_values_are_truncated(self) -> None:
        long_word = "x" * 200
        self.parse(text=f"a = {long_word}\n")
        words = [values[0] for label, values in self.rows() if label == "WORD"]
        self.assertEqual(len(words[0]), ide.MAX_TREE_VALUE_CHARS)
        self.assertTrue(words[0].endswith("..."))

    def test_tree_is_truncated_past_the_node_cap(self) -> None:
        original = ide.MAX_TREE_NODES
        ide.MAX_TREE_NODES = 5
        try:
            self.parse()
            self.assertIn("truncated", self.status())
            self.assertIn("truncated", self.rows()[-1][0])
        finally:
            ide.MAX_TREE_NODES = original

    def test_errors_clear_the_tree_and_return_to_the_text_view(self) -> None:
        self.parse()
        self.app.result_pane.select_view(ide.TREE_VIEW)
        self.parse(text="a = ?\n")
        self.assertEqual(self.app.result_pane.tree.get_children(), ())
        self.assertEqual(self.app.result_pane.current_view(), ide.TEXT_VIEW)

    def test_collapse_and_expand_all(self) -> None:
        self.parse()
        pane = self.app.result_pane
        pane.collapse_all()
        self.assertFalse(any(pane.tree.item(i, "open") for i in pane._walk()))
        pane.expand_all()
        self.assertTrue(all(pane.tree.item(i, "open") for i in pane._walk()))

    def test_menu_and_notebook_stay_in_sync(self) -> None:
        self.app.result_view.set(ide.TREE_VIEW)
        self.app.on_view_selected()
        self.app.update()
        self.assertEqual(self.app.result_pane.current_view(), ide.TREE_VIEW)
        self.app.result_pane.notebook.select(0)
        self.app.update()
        self.assertEqual(self.app.result_view.get(), ide.TEXT_VIEW)


class TestHighlighting(IdeTestCase):
    def tags_present(self) -> set[str]:
        text = self.app.grammar_pane.text
        return {name for name in ide.SYNTAX_COLOURS if text.tag_ranges(name)}

    def test_every_token_class_is_coloured(self) -> None:
        self.app.grammar_pane.set_content(GRAMMAR + "high.2: NUMBER\n")
        self.app.highlighter.highlight()
        self.app.update()
        expected = {"comment", "directive", "string", "regexp", "terminal", "rule", "number", "operator"}
        self.assertEqual(expected - self.tags_present(), set())

    def test_a_comment_is_not_mistaken_for_a_regexp(self) -> None:
        self.app.grammar_pane.set_content("// not /a regexp/\n")
        self.app.highlighter.highlight()
        self.app.update()
        self.assertEqual(self.tags_present(), {"comment"})

    def tag_per_character(self) -> list[str | None]:
        """Which tag covers each character, read back out of the widget."""
        text = self.app.grammar_pane.text
        content = self.app.grammar_pane.content()
        covered: list[str | None] = [None] * len(content)
        for name in ide.SYNTAX_COLOURS:
            ranges = text.tag_ranges(name)
            for start, end in zip(ranges[0::2], ranges[1::2]):
                # count() returns None, not (0,), for an empty range such as the very start
                first = (text.count("1.0", start, "chars") or (0,))[0]
                last = (text.count("1.0", end, "chars") or (0,))[0]
                for offset in range(first, last):
                    covered[offset] = name
        return covered

    @staticmethod
    def expected_per_character(content: str) -> list[str | None]:
        expected: list[str | None] = [None] * len(content)
        for match in ide.SYNTAX_PATTERN.finditer(content):
            if match.lastgroup:
                for offset in range(match.start(), match.end()):
                    expected[offset] = match.lastgroup
        return expected

    def test_every_character_gets_the_tag_the_regex_gave_it(self) -> None:
        """Guards the offset-to-index mapping: a wrong line or column shifts tags off their text."""
        grammar = (
            '// a comment\nstart: pair+\npair: NAME "=" (value)\nvalue: NUMBER | /[a-z]+/i\n%import common.NUMBER\n'
        )
        self.app.grammar_pane.set_content(grammar)
        self.app.highlighter.highlight()
        self.app.update()
        content = self.app.grammar_pane.content()
        self.assertEqual(self.tag_per_character(), self.expected_per_character(content))

    def test_tags_on_later_lines_land_on_the_right_text(self) -> None:
        self.app.grammar_pane.set_content("\n" * 40 + "%ignore WS\n")
        self.app.highlighter.highlight()
        self.app.update()
        text = self.app.grammar_pane.text
        ranges = text.tag_ranges("directive")
        self.assertEqual(text.get(ranges[0], ranges[1]), "%ignore")

    def test_highlighting_scales_with_the_grammar(self) -> None:
        """It used to address tags as offsets from '1.0', which Tk resolves by counting from the
        start of the widget: O(matches x length), minutes on a few thousand lines."""
        unit = 'rule{n}: NAME "=" /[0-9]+/  // note\n%import common.WS\n'
        small = "".join(unit.format(n=i) for i in range(200))
        large = "".join(unit.format(n=i) for i in range(800))

        def elapsed(content: str) -> float:
            self.app.grammar_pane.set_content(content)
            self.app.update_idletasks()
            start = time.perf_counter()
            self.app.highlighter.highlight()
            self.app.update_idletasks()
            return time.perf_counter() - start

        first = elapsed(small)
        fourfold = elapsed(large)
        self.assertLess(fourfold, 3.0, "a 1600-line grammar should not take seconds to colour")
        # Linear work would roughly quadruple; the old quadratic version rose about eighteenfold.
        self.assertLess(fourfold, max(first, 0.01) * 8, f"{first:.3f}s then {fourfold:.3f}s looks quadratic")

    def test_highlighting_is_rebuilt_not_accumulated(self) -> None:
        self.app.grammar_pane.set_content("%ignore WS\n")
        self.app.highlighter.highlight()
        self.app.grammar_pane.set_content("rule: NAME\n")
        self.app.highlighter.highlight()
        self.app.update()
        self.assertNotIn("directive", self.tags_present())


class TestFiles(IdeTestCase):
    def test_load_save_round_trip_and_dirty_tracking(self) -> None:
        path = self.tmp / "demo.lark"
        path.write_text(GRAMMAR, encoding="utf-8")
        self.app.grammar_pane.load_path(path)
        self.app.update()
        self.assertFalse(self.app.grammar_pane.dirty)
        self.assertIn("demo.lark", self.app.grammar_pane.header.cget("text"))

        self.app.grammar_pane.text.insert("end", "// added\n")
        self.app.update()
        self.assertTrue(self.app.grammar_pane.dirty)
        self.assertTrue(self.app.grammar_pane.header.cget("text").endswith("*"))

        self.app.grammar_pane.save()
        self.app.update()
        self.assertFalse(self.app.grammar_pane.dirty)
        self.assertIn("// added", path.read_text(encoding="utf-8"))

    def test_clean_pane_never_prompts(self) -> None:
        self.assertTrue(self.app.grammar_pane.confirm_discard())


class TestSettings(IdeTestCase):
    def test_recent_files_are_remembered_and_restored(self) -> None:
        grammar = self.tmp / "demo.lark"
        grammar.write_text(GRAMMAR, encoding="utf-8")
        text = self.tmp / "demo.txt"
        text.write_text(INPUT, encoding="utf-8")

        self.app.open_recent(ide.GRAMMAR_KIND, grammar)
        self.app.open_recent(ide.INPUT_KIND, text)
        self.app.update()
        self.assertIn("Parsed OK", self.status())

        self.app._store_layout()
        self.app.settings.save()
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["recent_grammar"], [str(grammar.resolve())])
        self.assertEqual(len(saved["sash_fractions"]), 2)

        self.app.destroy()
        self.app = self.new_app()
        menu = self.app.recent_menus[ide.GRAMMAR_KIND][0]
        self.assertEqual(menu.entrycget(0, "label"), str(grammar.resolve()))

    def test_parser_choice_survives_a_restart(self) -> None:
        self.app.parser_name.set("lalr")
        self.app.on_parser_changed()
        self.app.destroy()
        self.app = self.new_app()
        self.assertEqual(self.app.parser_name.get(), "lalr")

    def test_missing_files_drop_out_of_the_recent_list(self) -> None:
        gone = self.tmp / "gone.lark"
        gone.write_text(GRAMMAR, encoding="utf-8")
        self.app.settings.remember(ide.GRAMMAR_KIND, gone)
        gone.unlink()
        self.assertEqual(self.app.settings.recent(ide.GRAMMAR_KIND), [])

    def test_recent_list_is_most_recent_first_and_deduplicated(self) -> None:
        first = self.tmp / "one.lark"
        second = self.tmp / "two.lark"
        for path in (first, second):
            path.write_text(GRAMMAR, encoding="utf-8")
        self.app.settings.remember(ide.GRAMMAR_KIND, first)
        self.app.settings.remember(ide.GRAMMAR_KIND, second)
        self.app.settings.remember(ide.GRAMMAR_KIND, first)
        self.assertEqual(
            self.app.settings.recent(ide.GRAMMAR_KIND),
            [str(first.resolve()), str(second.resolve())],
        )

    def test_recent_list_is_capped(self) -> None:
        for index in range(ide.MAX_RECENT + 5):
            path = self.tmp / f"g{index}.lark"
            path.write_text(GRAMMAR, encoding="utf-8")
            self.app.settings.remember(ide.GRAMMAR_KIND, path)
        self.assertEqual(len(self.app.settings.recent(ide.GRAMMAR_KIND)), ide.MAX_RECENT)

    def test_clearing_the_recent_list(self) -> None:
        path = self.tmp / "demo.lark"
        path.write_text(GRAMMAR, encoding="utf-8")
        self.app.settings.remember(ide.GRAMMAR_KIND, path)
        self.app._rebuild_recent_menu(ide.GRAMMAR_KIND)
        self.app.clear_recent(ide.GRAMMAR_KIND)
        for menu in self.app.recent_menus[ide.GRAMMAR_KIND]:
            self.assertEqual(menu.entrycget(0, "label"), "(empty)")

    def test_corrupt_settings_file_is_ignored(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(ide.Settings(self.settings_path).data, {})

    def test_wrongly_typed_setting_falls_back_to_the_default(self) -> None:
        settings = ide.Settings(self.settings_path)
        settings.set("parser", 17)
        self.assertEqual(settings.get("parser", ide.DEFAULT_PARSER), ide.DEFAULT_PARSER)


class TestContextMenus(IdeTestCase):
    """The editable panes offer their file commands on right click."""

    def labels(self, pane) -> list[str]:
        menu = pane.menu
        return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]

    def test_grammar_pane_offers_file_and_edit_commands(self) -> None:
        labels = self.labels(self.app.grammar_pane)
        for expected in ("New Grammar", "Open Grammar...", "Save Grammar", "Save Grammar As...", "Cut", "Paste"):
            self.assertIn(expected, labels)

    def test_input_pane_offers_the_corpus_shortcut(self) -> None:
        self.assertIn("Add Input as Case...", self.labels(self.app.input_pane))

    def test_file_commands_are_replaced_not_appended(self) -> None:
        pane = self.app.grammar_pane
        before = pane.menu.index("end")
        replaced = pane.file_command_count
        pane.set_file_commands([("New Grammar", pane.new_file)])
        self.assertEqual(pane.file_command_count, 2)  # the command plus its separator
        self.assertEqual(pane.menu.index("end"), before - replaced + 2)
        self.assertEqual(pane.menu.entrycget(0, "label"), "New Grammar")

    def test_context_menu_select_all(self) -> None:
        self.app.grammar_pane.set_content(GRAMMAR)
        self.app.grammar_pane.select_all()
        self.app.update()
        selected = self.app.grammar_pane.text.get("sel.first", "sel.last")
        self.assertEqual(selected, GRAMMAR)


class TestCorpusModel(IdeTestCase):
    """The corpus file format and runner, independent of the window."""

    def parser(self, grammar: str = GRAMMAR):
        self.app.grammar_pane.set_content(grammar)
        return self.app.build_parser(announce=False)

    def test_default_path_sits_next_to_the_grammar(self) -> None:
        self.assertEqual(
            ide.Corpus.default_path_for(Path("/tmp/demo.lark")),
            Path("/tmp/demo.corpus.json"),
        )

    def test_save_and_load_round_trip(self) -> None:
        corpus = ide.Corpus()
        corpus.add(ide.CorpusCase(name="one", text=INPUT, tree="start"))
        corpus.add(ide.CorpusCase(name="two", text="a =", expect=ide.EXPECT_ERROR))
        path = self.tmp / "demo.corpus.json"
        corpus.save(path)
        self.assertFalse(corpus.dirty)

        reloaded = ide.Corpus()
        reloaded.load(path)
        self.assertEqual(reloaded.cases, corpus.cases)
        self.assertEqual(reloaded.path, path)

    def test_transient_results_are_not_persisted(self) -> None:
        corpus = ide.Corpus()
        case = ide.CorpusCase(name="one", text=INPUT)
        case.result, case.detail = ide.RESULT_FAIL, "some detail"
        corpus.add(case)
        path = self.tmp / "demo.corpus.json"
        corpus.save(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(payload["cases"][0]), {"name", "input", "expect"})

    def test_a_malformed_corpus_raises_value_error(self) -> None:
        path = self.tmp / "bad.corpus.json"
        path.write_text('{"version": 1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            ide.Corpus().load(path)

    def test_unknown_expect_value_falls_back_to_parse(self) -> None:
        case = ide.CorpusCase.from_dict({"name": "x", "input": "a", "expect": "nonsense"})
        self.assertEqual(case.expect, ide.EXPECT_PARSE)

    def test_duplicate_names_are_made_unique(self) -> None:
        corpus = ide.Corpus()
        corpus.add(ide.CorpusCase(name="one", text=""))
        self.assertEqual(corpus.unique_name("one"), "one (2)")
        self.assertEqual(corpus.unique_name("other"), "other")

    def test_a_case_that_parses_passes(self) -> None:
        cases = [ide.CorpusCase(name="ok", text=INPUT)]
        self.assertEqual(ide.run_corpus(ide.single_parser(self.parser()), cases), (1, 1))
        self.assertEqual(cases[0].result, ide.RESULT_PASS)

    def test_a_case_that_does_not_parse_fails(self) -> None:
        cases = [ide.CorpusCase(name="bad", text="a = ?")]
        self.assertEqual(ide.run_corpus(ide.single_parser(self.parser()), cases), (0, 1))
        self.assertEqual(cases[0].result, ide.RESULT_FAIL)
        self.assertTrue(cases[0].detail)

    def test_an_error_case_passes_when_it_fails_to_parse(self) -> None:
        cases = [ide.CorpusCase(name="bad", text="a = ?", expect=ide.EXPECT_ERROR)]
        self.assertEqual(ide.run_corpus(ide.single_parser(self.parser()), cases), (1, 1))

    def test_an_error_case_fails_when_it_parses(self) -> None:
        cases = [ide.CorpusCase(name="fine", text=INPUT, expect=ide.EXPECT_ERROR)]
        self.assertEqual(ide.run_corpus(ide.single_parser(self.parser()), cases), (0, 1))
        self.assertIn("error was expected", cases[0].detail)

    def test_a_recorded_tree_is_compared(self) -> None:
        parser = self.parser()
        recorded = parser.parse(INPUT).pretty().rstrip("\n")
        good = [ide.CorpusCase(name="ok", text=INPUT, tree=recorded)]
        self.assertEqual(ide.run_corpus(ide.single_parser(parser), good), (1, 1))

        stale = [ide.CorpusCase(name="stale", text=INPUT, tree="start\n  something_else")]
        self.assertEqual(ide.run_corpus(ide.single_parser(parser), stale), (0, 1))
        self.assertIn("differs", stale[0].detail)

    def test_a_grammar_change_that_keeps_parsing_still_fails_a_recorded_case(self) -> None:
        """The regression the recorded tree exists to catch: it still parses, but differently."""
        parser = self.parser()
        recorded = parser.parse("a = 1\n").pretty().rstrip("\n")
        renamed = GRAMMAR.replace("pair:", "binding:").replace("start: pair+", "start: binding+")
        cases = [ide.CorpusCase(name="ok", text="a = 1\n", tree=recorded)]
        self.assertEqual(ide.run_corpus(ide.single_parser(self.parser(renamed)), cases), (0, 1))


class TestCorpusInTheApp(IdeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app.grammar_pane.set_content(GRAMMAR)
        self.app.input_pane.set_content(INPUT)

    def test_run_fills_the_corpus_tab(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        self.app.corpus.add(ide.CorpusCase(name="bad", text="a = ?"))
        self.app.run_corpus_now()
        self.app.update()
        view = self.app.result_pane.corpus_view
        rows = [view.tree.item(i, "values") for i in view.tree.get_children()]
        self.assertEqual([row[0] for row in rows], ["ok", "bad"])
        self.assertEqual([row[4] for row in rows], [ide.RESULT_PASS, ide.RESULT_FAIL])
        self.assertIn("1/2 passed", view.summary.cget("text"))

    def test_a_broken_grammar_does_not_run_the_corpus(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        self.app.grammar_pane.set_content("start: MISSING\n")
        self.app.run_corpus_now()
        self.app.update()
        self.assertIn("does not compile", self.app.result_pane.corpus_view.summary.cget("text"))

    def test_running_on_every_parse_can_be_switched_off(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        self.app.run_corpus_on_parse.set(False)
        self.app.parse_now()
        self.app.update()
        self.assertEqual(self.detail(), "")
        self.app.run_corpus_on_parse.set(True)
        self.app.parse_now()
        self.app.update()
        self.assertIn("corpus:", self.detail())

    def test_opening_a_grammar_adopts_the_corpus_beside_it(self) -> None:
        grammar = self.tmp / "demo.lark"
        grammar.write_text(GRAMMAR, encoding="utf-8")
        corpus = ide.Corpus()
        corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        corpus.save(ide.Corpus.default_path_for(grammar))

        self.app.open_recent(ide.GRAMMAR_KIND, grammar)
        self.app.update()
        self.assertEqual([c.name for c in self.app.corpus.cases], ["ok"])
        self.assertEqual(self.detail(), "corpus: all 1 passed")

    def test_opening_a_grammar_without_a_corpus_leaves_the_current_one(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="kept", text=INPUT))
        self.app.corpus.dirty = False
        grammar = self.tmp / "lonely.lark"
        grammar.write_text(GRAMMAR, encoding="utf-8")
        self.app.open_recent(ide.GRAMMAR_KIND, grammar)
        self.app.update()
        self.assertEqual([c.name for c in self.app.corpus.cases], ["kept"])

    def test_double_click_loads_a_case_into_the_input_pane(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="other", text="z = 9\n"))
        self.app.open_corpus_case(0)
        self.app.update()
        self.assertEqual(self.app.input_pane.content(), "z = 9\n")
        self.assertIsNone(self.app.input_pane.path)

    def test_recording_an_expected_tree(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        self.app.run_corpus_now()
        self.app.result_pane.corpus_view.tree.selection_set("0")
        self.app.record_expected_tree()
        self.app.update()
        self.assertIsNotNone(self.app.corpus.cases[0].tree)
        self.assertTrue(self.app.corpus.dirty)
        self.assertEqual(self.app.corpus.cases[0].result, ide.RESULT_PASS)

        self.app.clear_expected_tree()
        self.assertIsNone(self.app.corpus.cases[0].tree)

    def test_recording_is_refused_for_an_error_case(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="bad", text="a = ?", expect=ide.EXPECT_ERROR))
        self.app.run_corpus_now()
        self.app.result_pane.corpus_view.tree.selection_set("0")
        self.app.record_expected_tree()
        self.app.update()
        self.assertIsNone(self.app.corpus.cases[0].tree)
        self.assertIn("no expected tree", self.status())

    def test_commands_need_a_selection(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        self.app.run_corpus_now()
        self.app.result_pane.corpus_view.tree.selection_set(())
        self.app.delete_case()
        self.app.update()
        self.assertEqual(len(self.app.corpus.cases), 1)
        self.assertIn("Select a case", self.status())

    def test_deleting_a_case(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="one", text=INPUT))
        self.app.corpus.add(ide.CorpusCase(name="two", text=INPUT))
        self.app.run_corpus_now()
        self.app.result_pane.corpus_view.tree.selection_set("0")
        self.app.delete_case()
        self.app.update()
        self.assertEqual([c.name for c in self.app.corpus.cases], ["two"])

    def test_saving_and_reopening_a_corpus(self) -> None:
        path = self.tmp / "demo.corpus.json"
        self.app.corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        self.app._write_corpus(path)
        self.app.update()
        self.assertFalse(self.app.corpus.dirty)
        self.assertIn("Corpus saved", self.status())

        self.app.new_corpus()
        self.assertEqual(self.app.corpus.cases, [])
        self.assertTrue(self.app.load_corpus(path, announce=False))
        self.assertEqual([c.name for c in self.app.corpus.cases], ["ok"])

    def test_loading_a_bad_corpus_reports_failure_without_raising(self) -> None:
        path = self.tmp / "bad.corpus.json"
        path.write_text("not json at all", encoding="utf-8")
        self.assertFalse(self.app.load_corpus(path, announce=False))

    def test_corpus_view_is_reachable_from_the_view_menu(self) -> None:
        self.app.result_view.set(ide.CORPUS_VIEW)
        self.app.on_view_selected()
        self.app.update()
        self.assertEqual(self.app.result_pane.current_view(), ide.CORPUS_VIEW)


class TestParserBadge(IdeTestCase):
    """The active parser and start rule are shown above the grammar pane."""

    def test_badge_shows_the_defaults_at_startup(self) -> None:
        self.assertIn(f"parser: {ide.DEFAULT_PARSER}", self.badge())
        self.assertIn(f"start: {ide.DEFAULT_START_RULE}", self.badge())

    def test_badge_follows_the_parser_choice(self) -> None:
        self.app.parser_name.set("lalr")
        self.app.on_parser_changed()
        self.app.update()
        self.assertIn("parser: lalr", self.badge())

    def test_badge_follows_the_start_rule(self) -> None:
        self.app.start_rule.set("pair")
        self.app._refresh_parser_badge()
        self.app.update()
        self.assertIn("start: pair", self.badge())

    def test_badge_survives_a_restart(self) -> None:
        self.app.parser_name.set("lalr")
        self.app.on_parser_changed()
        self.app.destroy()
        self.app = self.new_app()
        self.assertIn("parser: lalr", self.badge())


class TestNodeSpans(IdeTestCase):
    """Selecting a node in the tree view marks the text it came from."""

    def setUp(self) -> None:
        super().setUp()
        self.parse(text='a = 1\nbb = "two"\n')
        self.app.result_pane.select_view(ide.TREE_VIEW)

    def item_named(self, label: str, occurrence: int = 0) -> str:
        pane = self.app.result_pane
        matches = [i for i in pane._walk() if pane.tree.item(i, "text") == label]
        return matches[occurrence]

    def span_text(self) -> str:
        ranges = self.app.input_pane.text.tag_ranges(ide.SPAN_TAG)
        if not ranges:
            return ""
        return self.app.input_pane.text.get(ranges[0], ranges[1])

    def test_selecting_a_token_marks_just_that_token(self) -> None:
        self.app.result_pane.tree.selection_set(self.item_named("NAME", 1))
        self.app.update()
        self.assertEqual(self.span_text(), "bb")

    def test_selecting_a_rule_marks_the_whole_rule(self) -> None:
        self.app.result_pane.tree.selection_set(self.item_named("pair", 1))
        self.app.update()
        self.assertEqual(self.span_text(), 'bb = "two"')

    def test_selecting_the_root_marks_everything(self) -> None:
        self.app.result_pane.tree.selection_set(self.item_named("start"))
        self.app.update()
        self.assertEqual(self.span_text(), 'a = 1\nbb = "two"')

    def test_clearing_the_selection_clears_the_span(self) -> None:
        self.app.result_pane.tree.selection_set(self.item_named("NAME"))
        self.app.update()
        self.assertTrue(self.span_text())
        self.app.result_pane.tree.selection_set(())
        self.app.update()
        self.assertEqual(self.span_text(), "")

    def test_a_new_parse_clears_the_span(self) -> None:
        self.app.result_pane.tree.selection_set(self.item_named("NAME"))
        self.app.update()
        self.app.parse_now()
        self.app.update()
        self.assertEqual(self.span_text(), "")

    def test_spans_are_refused_once_the_input_has_changed(self) -> None:
        """A stale span would point at the wrong text, which is worse than no span at all."""
        item = self.item_named("NAME", 1)
        self.app.input_pane.text.insert("1.0", "zzz = 0\n")
        self.app.result_pane.tree.selection_set(item)
        self.app.update()
        self.assertEqual(self.span_text(), "")
        self.assertIn("changed since the last parse", self.status())

    def test_node_span_helper_handles_a_node_without_position(self) -> None:
        self.assertIsNone(ide.node_span(object()))


class TestGrammarImports(IdeTestCase):
    """%import resolves against the directory the grammar was loaded from."""

    MAIN = 'start: pair+\npair: NAME "=" value\n%import .shared.value\n%import common.CNAME -> NAME\n%import common.WS\n%ignore WS\n'
    SHARED = "value: NUMBER\n%import common.NUMBER\n"

    def write_pair(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "shared.lark").write_text(self.SHARED, encoding="utf-8")
        main = directory / "main.lark"
        main.write_text(self.MAIN, encoding="utf-8")
        return main

    def test_a_relative_import_resolves_once_the_grammar_is_loaded(self) -> None:
        main = self.write_pair(self.tmp / "project")
        self.app.open_recent(ide.GRAMMAR_KIND, main)
        self.app.input_pane.set_content("a = 1\n")
        self.app.parse_now()
        self.app.update()
        self.assertIn("Parsed OK", self.status())

    def test_a_plain_import_of_a_sibling_grammar_also_resolves(self) -> None:
        main = self.write_pair(self.tmp / "plain")
        main.write_text(self.MAIN.replace("%import .shared.value", "%import shared.value"), encoding="utf-8")
        self.app.open_recent(ide.GRAMMAR_KIND, main)
        self.app.input_pane.set_content("a = 1\n")
        self.app.parse_now()
        self.app.update()
        self.assertIn("Parsed OK", self.status())

    def test_the_import_directory_follows_the_grammar(self) -> None:
        """Two projects can define the same module differently; the loaded grammar decides which."""
        first = self.write_pair(self.tmp / "first")
        second = self.write_pair(self.tmp / "second")
        (second.parent / "shared.lark").write_text("value: ESCAPED_STRING\n%import common.ESCAPED_STRING\n", "utf-8")

        self.app.open_recent(ide.GRAMMAR_KIND, first)
        self.app.input_pane.set_content("a = 1\n")
        self.app.parse_now()
        self.app.update()
        self.assertIn("Parsed OK", self.status())

        self.app.open_recent(ide.GRAMMAR_KIND, second)
        self.app.parse_now()
        self.app.update()
        self.assertIn("Parse error", self.status())

    def test_an_unsaved_grammar_explains_why_the_import_failed(self) -> None:
        self.app.grammar_pane.set_content(self.MAIN)
        self.app.parse_now()
        self.app.update()
        self.assertIn("Grammar error", self.status())
        self.assertIn("has not been saved", self.app.result_pane.text.get("1.0", "end"))

    def test_no_import_options_without_a_file(self) -> None:
        self.assertEqual(self.app._import_options(), {})

    def test_import_options_point_at_the_grammar_directory(self) -> None:
        main = self.write_pair(self.tmp / "opts")
        self.app.grammar_pane.load_path(main)
        options = self.app._import_options()
        self.assertEqual(options["source_path"], str(main))
        self.assertEqual(options["import_paths"], [str(main.parent)])


class TestFindingNodesFromTheInput(IdeTestCase):
    """The reverse of the span highlight: from a position in the input to a tree node."""

    def setUp(self) -> None:
        super().setUp()
        self.parse(text='a = 1\nbb = "two"\n')

    def label(self) -> str:
        selection = self.app.result_pane.tree.selection()
        return self.app.result_pane.tree.item(selection[0], "text") if selection else ""

    def put_cursor(self, index: str) -> None:
        self.app.input_pane.text.mark_set("insert", index)
        self.app.on_input_cursor_moved()
        self.app.update()

    def select_text(self, first: str, last: str) -> None:
        self.app.input_pane.text.tag_add("sel", first, last)
        self.app.on_input_cursor_moved()
        self.app.update()

    def test_the_caret_in_a_token_finds_that_token(self) -> None:
        self.put_cursor("2.0")
        self.assertEqual(self.label(), "NAME")

    def test_the_caret_in_a_value_finds_the_value(self) -> None:
        self.put_cursor("1.4")
        self.assertEqual(self.label(), "NUMBER")

    def test_a_selection_spanning_a_whole_line_finds_the_rule(self) -> None:
        self.select_text("2.0", "2.10")
        self.assertEqual(self.label(), "pair")

    def test_a_selection_spanning_both_lines_finds_the_root(self) -> None:
        self.select_text("1.0", "2.10")
        self.assertEqual(self.label(), "start")

    def test_the_deepest_node_wins(self) -> None:
        """The caret sits inside start, pair and NAME at once; NAME is the useful answer."""
        self.put_cursor("1.0")
        self.assertEqual(self.label(), "NAME")

    def test_following_the_cursor_can_be_switched_off(self) -> None:
        self.app.follow_cursor.set(False)
        self.put_cursor("2.0")
        self.assertEqual(self.label(), "")

    def test_the_explicit_command_works_even_when_following_is_off(self) -> None:
        self.app.follow_cursor.set(False)
        self.app.input_pane.text.mark_set("insert", "2.0")
        self.app.find_node_at_cursor()
        self.app.update()
        self.assertEqual(self.label(), "NAME")
        self.assertEqual(self.app.result_pane.current_view(), ide.TREE_VIEW)

    def test_the_explicit_command_refuses_a_stale_tree(self) -> None:
        self.app.input_pane.text.insert("1.0", "zzz = 0\n")
        self.app.find_node_at_cursor()
        self.app.update()
        self.assertIn("changed since the last parse", self.status())

    def test_a_position_outside_every_node_selects_nothing(self) -> None:
        self.app.input_pane.set_content("   \n")
        self.app.parse_now()
        self.app.update()
        self.app.find_node_at_cursor()
        self.app.update()
        self.assertEqual(self.label(), "")

    def test_selecting_a_node_still_highlights_back_into_the_input(self) -> None:
        """The two directions must agree: going one way and back lands on the same text."""
        self.put_cursor("2.0")
        ranges = self.app.input_pane.text.tag_ranges(ide.SPAN_TAG)
        self.assertEqual(self.app.input_pane.text.get(ranges[0], ranges[1]), "bb")


class TestWatchingImports(IdeTestCase):
    MAIN = 'start: pair+\npair: NAME "=" value\n%import .shared.value\n%import common.CNAME -> NAME\n%import common.WS\n%ignore WS\n'
    NUMBERS = "value: NUMBER\n%import common.NUMBER\n"
    STRINGS = "value: ESCAPED_STRING\n%import common.ESCAPED_STRING\n"

    def setUp(self) -> None:
        super().setUp()
        self.project = self.tmp / "project"
        self.project.mkdir(parents=True, exist_ok=True)
        self.shared = self.project / "shared.lark"
        self.shared.write_text(self.NUMBERS, encoding="utf-8")
        self.main = self.project / "main.lark"
        self.main.write_text(self.MAIN, encoding="utf-8")
        self.app.open_recent(ide.GRAMMAR_KIND, self.main)
        self.app.input_pane.set_content("a = 1\n")
        self.app.parse_now()
        self.app.update()

    def touch_shared(self, text: str) -> None:
        self.shared.write_text(text, encoding="utf-8")
        os.utime(self.shared, (time.time() + 5, time.time() + 5))

    def test_the_imported_file_is_watched(self) -> None:
        self.assertIn(self.shared, self.app._import_mtimes)

    def test_bundled_grammars_are_not_watched(self) -> None:
        """common.lark is a package resource, not a path, so there is nothing to stat."""
        self.assertEqual(list(self.app._import_mtimes), [self.shared])

    def test_a_changed_import_triggers_a_reparse(self) -> None:
        self.assertIn("Parsed OK", self.status())
        self.touch_shared(self.STRINGS)
        self.app.check_imports()
        self.app.update()
        self.assertIn("shared.lark changed on disk", self.status())
        self.assertIn("Parse error", self.app.result_pane.text.get("1.0", "end"))

    def test_an_unchanged_import_does_nothing(self) -> None:
        before = self.status()
        self.app.check_imports()
        self.app.update()
        self.assertEqual(self.status(), before)

    def test_a_deleted_import_counts_as_a_change(self) -> None:
        self.shared.unlink()
        self.app.check_imports()
        self.app.update()
        self.assertIn("changed on disk", self.status())

    def test_watching_can_be_switched_off(self) -> None:
        self.app.watch_imports.set(False)
        before = self.status()
        self.touch_shared(self.STRINGS)
        self.app.check_imports()
        self.app.update()
        self.assertEqual(self.status(), before)

    def test_the_watch_list_follows_the_grammar(self) -> None:
        self.app.grammar_pane.set_content('start: "a"\n')
        self.app.parse_now()
        self.app.update()
        self.assertEqual(self.app._import_mtimes, {})

    def test_an_unsaved_grammar_watches_nothing(self) -> None:
        self.assertEqual(self.new_app()._imported_files(), [])


class TestPerCaseParserOptions(IdeTestCase):
    """A case may override the parser or the start rule it runs under."""

    def setUp(self) -> None:
        super().setUp()
        self.app.grammar_pane.set_content(GRAMMAR)
        self.app.input_pane.set_content(INPUT)

    def run_cases(self) -> tuple[int, int]:
        return ide.run_corpus(self.app._case_parser_supplier(), self.app.corpus.cases)

    def test_a_case_with_its_own_start_rule(self) -> None:
        """'a = 1' is not a whole document, but it is a valid pair."""
        self.app.corpus.add(ide.CorpusCase(name="fragment", text="a = 1", start="pair"))
        self.assertEqual(self.run_cases(), (1, 1))

    def test_the_same_fragment_fails_under_the_default_start_rule(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="fragment", text="a = 1", start="pair"))
        self.app.corpus.cases[0].start = None
        self.assertEqual(self.run_cases(), (1, 1))
        self.app.start_rule.set("value")
        self.assertEqual(self.run_cases(), (0, 1))

    def test_a_case_with_its_own_parser(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="under lalr", text=INPUT, parser="lalr"))
        self.assertEqual(self.run_cases(), (1, 1))

    def test_an_unknown_start_rule_fails_that_case_only(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="good", text=INPUT))
        self.app.corpus.add(ide.CorpusCase(name="bad rule", text=INPUT, start="nosuchrule"))
        self.assertEqual(self.run_cases(), (1, 2))
        self.assertIn("does not compile", self.app.corpus.cases[1].detail)
        self.assertIn("start=nosuchrule", self.app.corpus.cases[1].detail)

    def test_each_parser_combination_is_compiled_once(self) -> None:
        for index in range(4):
            self.app.corpus.add(ide.CorpusCase(name=f"c{index}", text=INPUT, parser="lalr"))
        compiled = []
        original = self.app._compile_grammar

        def counting(*args, **kwargs):
            compiled.append((args, kwargs))
            return original(*args, **kwargs)

        self.app._compile_grammar = counting
        self.run_cases()
        self.assertEqual(len(compiled), 1)

    def test_overrides_survive_a_save_and_load(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="fragment", text="a = 1", parser="lalr", start="pair"))
        path = self.tmp / "demo.corpus.json"
        self.app.corpus.save(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["cases"][0]["parser"], "lalr")
        self.assertEqual(payload["cases"][0]["start"], "pair")

        reloaded = ide.Corpus()
        reloaded.load(path)
        self.assertEqual(reloaded.cases[0].parser, "lalr")
        self.assertEqual(reloaded.cases[0].start, "pair")

    def test_a_case_without_overrides_writes_no_extra_keys(self) -> None:
        corpus = ide.Corpus()
        corpus.add(ide.CorpusCase(name="plain", text=INPUT))
        path = self.tmp / "plain.corpus.json"
        corpus.save(path)
        self.assertEqual(set(json.loads(path.read_text(encoding="utf-8"))["cases"][0]), {"name", "input", "expect"})

    def test_a_nonsense_parser_in_a_file_is_ignored(self) -> None:
        case = ide.CorpusCase.from_dict({"name": "x", "input": "a", "parser": "packrat"})
        self.assertIsNone(case.parser)

    def test_a_blank_start_rule_in_a_file_means_inherit(self) -> None:
        self.assertIsNone(ide.CorpusCase.from_dict({"name": "x", "input": "a", "start": "  "}).start)

    def test_the_overrides_show_in_the_corpus_tab(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="fragment", text="a = 1", parser="lalr", start="pair"))
        self.app.run_corpus_now()
        self.app.update()
        view = self.app.result_pane.corpus_view
        values = view.tree.item(view.tree.get_children()[0], "values")
        self.assertEqual(values[2], "lalr")
        self.assertEqual(values[3], "pair")


class TestTokenView(IdeTestCase):
    """The Tokens tab shows the lexer's output and the terminals behind it."""

    def setUp(self) -> None:
        super().setUp()
        self.parse(text="a = 1\n")

    def rows(self) -> list[tuple[str, tuple]]:
        view = self.app.result_pane.token_view
        return [(view.stream.item(i, "text"), view.stream.item(i, "values")) for i in view.stream.get_children()]

    def terminal_rows(self) -> dict[str, tuple]:
        view = self.app.result_pane.token_view
        return {view.terminals.item(i, "text"): view.terminals.item(i, "values") for i in view.terminals.get_children()}

    def test_the_token_stream_is_listed_in_order(self) -> None:
        self.assertEqual([label for label, _ in self.rows()][:3], ["NAME", "WS", "EQUAL"])

    def test_tokens_carry_value_and_position(self) -> None:
        values = dict(self.rows())["NAME"]
        self.assertEqual(values[0], "a")
        self.assertEqual(values[1], "1:1")

    def test_ignored_tokens_are_shown_but_marked(self) -> None:
        """WS is %ignored, so the parser never sees it, but the lexer did."""
        view = self.app.result_pane.token_view
        whitespace = [i for i in view.stream.get_children() if view.stream.item(i, "text") == "WS"]
        self.assertTrue(whitespace)
        self.assertIn("ignored", view.stream.item(whitespace[0], "tags"))

    def test_terminals_are_listed_with_their_use_counts(self) -> None:
        rows = self.terminal_rows()
        self.assertEqual(str(rows["NAME"][1]), "1")
        self.assertIn("ESCAPED_STRING", rows)
        self.assertEqual(str(rows["ESCAPED_STRING"][1]), "0")

    def test_an_unused_terminal_is_marked(self) -> None:
        view = self.app.result_pane.token_view
        unused = [i for i in view.terminals.get_children() if view.terminals.item(i, "text") == "ESCAPED_STRING"]
        self.assertIn("unused", view.terminals.item(unused[0], "tags"))

    def test_a_failed_parse_still_shows_tokens(self) -> None:
        """Lexing is separate from parsing, which is the point of the view."""
        self.parse(text="a = = 1\n")
        self.assertIn("Parse error", self.status())
        self.assertIn("NAME", [label for label, _ in self.rows()])

    def test_a_lexing_failure_shows_how_far_it_got(self) -> None:
        self.parse(text="a = \u00a7\n")
        view = self.app.result_pane.token_view
        self.assertIn("lexing stopped", view.summary.cget("text"))
        self.assertEqual(next(label for label, _ in self.rows()), "NAME")

    def test_selecting_a_token_highlights_it_in_the_input(self) -> None:
        view = self.app.result_pane.token_view
        view.stream.selection_set("0")
        self.app.update()
        ranges = self.app.input_pane.text.tag_ranges(ide.SPAN_TAG)
        self.assertEqual(self.app.input_pane.text.get(ranges[0], ranges[1]), "a")

    def test_the_tab_is_reachable_from_the_view_menu(self) -> None:
        self.app.result_view.set(ide.TOKEN_VIEW)
        self.app.on_view_selected()
        self.app.update()
        self.assertEqual(self.app.result_pane.current_view(), ide.TOKEN_VIEW)


AMBIGUOUS = 'start: expr\nexpr: expr "+" expr | NUM\n%import common.NUMBER -> NUM\n%import common.WS\n%ignore WS\n'


class TestAmbiguity(IdeTestCase):
    def test_ambiguity_is_resolved_silently_by_default(self) -> None:
        self.parse(grammar=AMBIGUOUS, text="1 + 2 + 3")
        self.assertIn("Parsed OK", self.status())
        self.assertNotIn("ambiguous", self.status())

    def test_explicit_ambiguity_reports_the_count(self) -> None:
        self.app.show_ambiguity.set(True)
        self.parse(grammar=AMBIGUOUS, text="1 + 2 + 3")
        self.assertIn("1 ambiguous node", self.status())

    def test_the_ambiguous_node_appears_in_the_tree(self) -> None:
        self.app.show_ambiguity.set(True)
        self.parse(grammar=AMBIGUOUS, text="1 + 2 + 3")
        pane = self.app.result_pane
        labels = [pane.tree.item(i, "text") for i in pane._walk()]
        self.assertIn("_ambig (2 derivations)", labels)
        self.assertEqual(len([label for label in labels if label.startswith("derivation ")]), 2)

    def test_the_ambiguous_node_is_tagged(self) -> None:
        self.app.show_ambiguity.set(True)
        self.parse(grammar=AMBIGUOUS, text="1 + 2 + 3")
        pane = self.app.result_pane
        ambig = [i for i in pane._walk() if pane.tree.item(i, "text").startswith("_ambig")]
        self.assertIn(ide.AMBIG_NODE, pane.tree.item(ambig[0], "tags"))

    def test_an_unambiguous_input_reports_nothing(self) -> None:
        self.app.show_ambiguity.set(True)
        self.parse(grammar=AMBIGUOUS, text="1")
        self.assertNotIn("ambiguous", self.status())

    def test_lalr_is_not_offered_the_option(self) -> None:
        """lalr raises a ConfigurationError if given ambiguity='explicit', so it never is."""
        self.app.show_ambiguity.set(True)
        self.app.parser_name.set("lalr")
        self.assertEqual(self.app._ambiguity_options("lalr"), {})
        self.parse(grammar=GRAMMAR)
        self.assertIn("Parsed OK", self.status())

    def test_switching_it_on_with_lalr_says_so(self) -> None:
        self.app.parser_name.set("lalr")
        self.app.grammar_pane.set_content(GRAMMAR)
        self.app.show_ambiguity.set(True)
        self.app.on_ambiguity_changed()
        self.app.update()
        self.assertIn("only reported by the earley parser", self.status())


class TestRailroadDiagrams(unittest.TestCase):
    """The SVG generator, which is a plain function and needs no window."""

    SIMPLE = 'start: pair+\npair: NAME "=" value\nvalue: NUMBER | ESCAPED_STRING\nNAME: /[a-z]+/\n'

    def items(self, grammar: str) -> dict:
        return dict(railroad.grammar_items(grammar))

    def test_every_rule_and_terminal_becomes_a_diagram(self) -> None:
        self.assertEqual(list(self.items(self.SIMPLE)), ["start", "pair", "value", "NAME"])

    def test_directives_are_not_diagrams(self) -> None:
        items = self.items(self.SIMPLE + "%import common.WS\n%ignore WS\n")
        self.assertNotIn("WS", [name for name in items if name.startswith("%")])
        self.assertEqual(len(items), 4)

    def test_a_choice_becomes_a_choice(self) -> None:
        self.assertIsInstance(self.items(self.SIMPLE)["value"], railroad.Choice)

    def test_a_sequence_becomes_a_sequence(self) -> None:
        self.assertIsInstance(self.items(self.SIMPLE)["pair"], railroad.Sequence)

    def test_plus_becomes_a_repeat(self) -> None:
        self.assertIsInstance(self.items(self.SIMPLE)["start"], railroad.Repeat)

    def test_star_becomes_an_optional_repeat(self) -> None:
        item = self.items('start: "a"*\n')["start"]
        self.assertIsInstance(item, railroad.Choice)
        self.assertIsInstance(item.items[0], railroad.Repeat)
        self.assertIsInstance(item.items[1], railroad.Skip)

    def test_question_mark_becomes_an_optional(self) -> None:
        item = self.items('start: "a"?\n')["start"]
        self.assertIsInstance(item, railroad.Choice)
        self.assertIsInstance(item.items[1], railroad.Skip)

    def test_square_brackets_become_an_optional(self) -> None:
        self.assertIsInstance(self.items('start: ["a"]\n')["start"], railroad.Choice)

    def test_a_repetition_range_is_labelled(self) -> None:
        item = self.items("start: DIGIT~2..4\nDIGIT: /[0-9]/\n")["start"]
        self.assertEqual(item.label, "2 to 4")

    def test_rules_and_terminals_are_drawn_differently(self) -> None:
        """A reader needs to tell 'go look at another rule' from 'match this text'."""
        pair = self.items(self.SIMPLE)["pair"]
        boxes = {item.text: item.is_rule for item in pair.items}
        self.assertFalse(boxes["NAME"])
        self.assertTrue(boxes["value"])

    def test_an_alias_does_not_change_the_shape(self) -> None:
        item = self.items('start: "a" -> named\n')["start"]
        self.assertIsInstance(item, railroad.Leaf)

    def test_sizes_are_positive_and_nest(self) -> None:
        outer = self.items(self.SIMPLE)["pair"]
        self.assertGreater(outer.width, max(child.width for child in outer.items))
        self.assertGreater(outer.up + outer.down, 0)

    def test_the_document_is_well_formed_svg(self) -> None:
        svg = railroad.grammar_svg(self.SIMPLE)
        parsed = ElementTree.fromstring(svg)
        self.assertTrue(parsed.tag.endswith("svg"))
        self.assertGreater(int(float(parsed.get("width"))), 0)
        self.assertGreater(int(float(parsed.get("height"))), 0)

    def test_every_rule_name_appears_as_a_title(self) -> None:
        svg = railroad.grammar_svg(self.SIMPLE)
        for name in ("start", "pair", "value", "NAME"):
            self.assertIn(f">{name}<", svg)

    def test_text_is_escaped(self) -> None:
        svg = railroad.grammar_svg('start: "<&>"\n')
        self.assertNotIn('>"<&>"<', svg)
        self.assertIn("&amp;", svg)

    def test_an_empty_grammar_still_produces_a_document(self) -> None:
        self.assertIn("<svg", railroad.grammar_svg("\n"))

    def test_a_broken_grammar_raises(self) -> None:
        with self.assertRaises(lark.exceptions.LarkError):
            railroad.grammar_svg("start: (((\n")


class TestRailroadExport(IdeTestCase):
    def test_exporting_writes_a_file(self) -> None:
        self.app.grammar_pane.set_content(GRAMMAR)
        target = self.tmp / "grammar.svg"
        self.assertTrue(self.app.write_railroad(target, railroad.grammar_svg(GRAMMAR)))
        self.assertIn("<svg", target.read_text(encoding="utf-8"))
        self.assertIn("written to grammar.svg", self.status())

    def test_an_empty_grammar_is_refused(self) -> None:
        self.app.grammar_pane.set_content("   ")
        self.app.export_railroad()
        self.app.update()
        self.assertIn("no grammar to draw", self.status())


class TestLayout(IdeTestCase):
    """Column widths. A stored layout must never be able to hide a column."""

    def pane_width(self) -> int:
        self.app.update_idletasks()
        return self.app.panes.winfo_width()

    def sashes(self) -> tuple[int, int]:
        self.app.update_idletasks()
        return self.app.panes.sashpos(0), self.app.panes.sashpos(1)

    def widths(self) -> list[int]:
        self.app.update_idletasks()
        return [p.winfo_width() for p in (self.app.grammar_pane, self.app.input_pane, self.app.result_pane)]

    def test_the_default_is_three_equal_columns(self) -> None:
        self.app.apply_sash_fractions(ide.DEFAULT_SASH_FRACTIONS)
        widths = self.widths()
        self.assertLess(max(widths) - min(widths), 20)

    def test_no_stored_layout_gives_the_default(self) -> None:
        self.assertEqual(self.app._stored_fractions(), ide.DEFAULT_SASH_FRACTIONS)

    def test_absolute_positions_from_older_versions_are_ignored(self) -> None:
        """They were saved in pixels against a window that may have been any size."""
        self.app.settings.set("sashes", [5, 10])
        self.assertEqual(self.app._stored_fractions(), ide.DEFAULT_SASH_FRACTIONS)

    def test_absolute_positions_are_dropped_on_save(self) -> None:
        self.app.settings.set("sashes", [5, 10])
        self.app._store_layout()
        self.assertNotIn("sashes", self.app.settings.data)

    def test_proportions_round_trip(self) -> None:
        self.app.apply_sash_fractions((0.25, 0.5))
        self.app._store_layout()
        first, second = self.app._stored_fractions()
        self.assertAlmostEqual(first, 0.25, places=1)
        self.assertAlmostEqual(second, 0.5, places=1)

    def test_out_of_order_proportions_are_ignored(self) -> None:
        self.app.settings.set("sash_fractions", [0.9, 0.2])
        self.assertEqual(self.app._stored_fractions(), ide.DEFAULT_SASH_FRACTIONS)

    def test_proportions_outside_the_window_are_ignored(self) -> None:
        for values in ([0.0, 0.5], [0.5, 1.0], [-0.2, 0.5], [0.5]):
            self.app.settings.set("sash_fractions", values)
            self.assertEqual(self.app._stored_fractions(), ide.DEFAULT_SASH_FRACTIONS, values)

    def test_garbage_proportions_are_ignored(self) -> None:
        self.app.settings.set("sash_fractions", ["x", None])
        self.assertEqual(self.app._stored_fractions(), ide.DEFAULT_SASH_FRACTIONS)

    def test_extreme_proportions_are_clamped_so_every_column_survives(self) -> None:
        self.app.apply_sash_fractions((0.97, 0.98))
        first, second = self.sashes()
        width = self.pane_width()
        self.assertLessEqual(first, width - 2 * ide.MIN_PANE_WIDTH)
        self.assertGreaterEqual(second - first, ide.MIN_PANE_WIDTH)
        self.assertGreaterEqual(width - second, ide.MIN_PANE_WIDTH - 1)

    def test_placing_reports_whether_it_worked(self) -> None:
        self.assertTrue(self.app.apply_sash_fractions((0.3, 0.6)))

    def test_restore_retries_until_the_window_is_mapped(self) -> None:
        """A compositing window manager may not have sized the window when the first try runs."""
        calls = []
        self.app.apply_sash_fractions = lambda fractions: calls.append(fractions) or False
        self.app._restore_sashes(attempts=3)
        self.assertIsNotNone(self.app._restore_job)
        self.assertEqual(len(calls), 1)

    def test_restore_gives_up_rather_than_retrying_forever(self) -> None:
        self.app.apply_sash_fractions = lambda _fractions: False
        self.app._restore_sashes(attempts=1)
        self.assertIsNone(self.app._restore_job)

    def test_reset_layout_restores_equal_columns(self) -> None:
        self.app.apply_sash_fractions((0.8, 0.9))
        self.app.reset_layout()
        widths = self.widths()
        self.assertLess(max(widths) - min(widths), 20)
        self.assertIn("Layout reset", self.status())
        self.assertEqual(self.app.settings.get("sash_fractions", []), list(ide.DEFAULT_SASH_FRACTIONS))

    def test_every_column_is_visible_after_a_restart(self) -> None:
        self.app.apply_sash_fractions((0.25, 0.5))
        self.app._store_layout()
        self.app.settings.save()
        self.app.destroy()
        self.app = self.new_app()
        self.app._restore_sashes()
        self.assertTrue(all(width > ide.MIN_PANE_WIDTH // 2 for width in self.widths()), self.widths())


class TestRecentInContextMenus(IdeTestCase):
    """The recent lists are offered on right click as well as from the File menu."""

    def entries(self, menu) -> list[str]:
        return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) != "separator"]

    def cascades(self, pane) -> list[str]:
        menu = pane.menu
        return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "cascade"]

    def write_files(self) -> tuple[Path, Path]:
        grammar = self.tmp / "demo.lark"
        grammar.write_text(GRAMMAR, encoding="utf-8")
        text = self.tmp / "demo.txt"
        text.write_text(INPUT, encoding="utf-8")
        return grammar, text

    def test_both_panes_offer_a_recent_submenu(self) -> None:
        self.assertIn("Recent Grammars", self.cascades(self.app.grammar_pane))
        self.assertIn("Recent Inputs", self.cascades(self.app.input_pane))

    def test_two_menus_are_registered_per_kind(self) -> None:
        """One in the File menu, one in the pane's context menu."""
        self.assertEqual(len(self.app.recent_menus[ide.GRAMMAR_KIND]), 2)
        self.assertEqual(len(self.app.recent_menus[ide.INPUT_KIND]), 2)

    def test_every_menu_for_a_kind_is_kept_up_to_date(self) -> None:
        grammar, _text = self.write_files()
        self.app.open_recent(ide.GRAMMAR_KIND, grammar)
        self.app.update()
        for menu in self.app.recent_menus[ide.GRAMMAR_KIND]:
            self.assertEqual(self.entries(menu)[0], str(grammar.resolve()))

    def test_clearing_empties_every_menu_for_that_kind(self) -> None:
        grammar, _text = self.write_files()
        self.app.open_recent(ide.GRAMMAR_KIND, grammar)
        self.app.clear_recent(ide.GRAMMAR_KIND)
        self.app.update()
        for menu in self.app.recent_menus[ide.GRAMMAR_KIND]:
            self.assertEqual(self.entries(menu), ["(empty)"])

    def test_the_two_kinds_stay_separate(self) -> None:
        grammar, text = self.write_files()
        self.app.open_recent(ide.GRAMMAR_KIND, grammar)
        self.app.open_recent(ide.INPUT_KIND, text)
        self.app.update()
        for menu in self.app.recent_menus[ide.INPUT_KIND]:
            self.assertNotIn(str(grammar.resolve()), self.entries(menu))


class TestStartupRestore(IdeTestCase):
    """With no files on the command line, the last ones used come back."""

    def remember(self) -> tuple[Path, Path]:
        grammar = self.tmp / "demo.lark"
        grammar.write_text(GRAMMAR, encoding="utf-8")
        text = self.tmp / "demo.txt"
        text.write_text(INPUT, encoding="utf-8")
        self.app.settings.remember(ide.GRAMMAR_KIND, grammar)
        self.app.settings.remember(ide.INPUT_KIND, text)
        self.app.settings.save()
        return grammar, text

    def test_the_last_files_are_reopened_and_parsed(self) -> None:
        grammar, text = self.remember()
        self.app.destroy()
        self.app = self.new_app()
        self.assertTrue(self.app.load_last_session())
        self.app.update()
        self.assertEqual(self.app.grammar_pane.path, grammar)
        self.assertEqual(self.app.input_pane.path, text)
        self.assertIn("Parsed OK", self.status())

    def test_restored_panes_are_not_dirty(self) -> None:
        self.remember()
        self.app.destroy()
        self.app = self.new_app()
        self.app.load_last_session()
        self.app.update()
        self.assertFalse(self.app.grammar_pane.dirty)
        self.assertFalse(self.app.input_pane.dirty)

    def test_the_grammar_is_highlighted_after_restoring(self) -> None:
        self.remember()
        self.app.destroy()
        self.app = self.new_app()
        self.app.load_last_session()
        self.app.update()
        self.assertTrue(self.app.grammar_pane.text.tag_ranges("directive"))

    def test_the_corpus_beside_the_grammar_comes_back_too(self) -> None:
        grammar, _text = self.remember()
        corpus = ide.Corpus()
        corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        corpus.save(ide.Corpus.default_path_for(grammar))
        self.app.destroy()
        self.app = self.new_app()
        self.app.load_last_session()
        self.app.update()
        self.assertEqual([case.name for case in self.app.corpus.cases], ["ok"])

    def test_nothing_remembered_means_nothing_loaded(self) -> None:
        self.assertFalse(self.app.load_last_session())
        self.assertIsNone(self.app.grammar_pane.path)

    def test_a_deleted_file_is_not_restored(self) -> None:
        grammar, _text = self.remember()
        grammar.unlink()
        self.app.destroy()
        self.app = self.new_app()
        self.app.load_last_session()
        self.app.update()
        self.assertIsNone(self.app.grammar_pane.path)
        self.assertIsNotNone(self.app.input_pane.path)


class TestCommandLine(unittest.TestCase):
    def test_no_arguments(self) -> None:
        args = ide.parse_args([])
        self.assertIsNone(args.grammar)
        self.assertIsNone(args.input)
        self.assertFalse(args.no_restore)

    def test_both_files(self) -> None:
        args = ide.parse_args(["a.lark", "b.txt"])
        self.assertEqual(args.grammar, Path("a.lark"))
        self.assertEqual(args.input, Path("b.txt"))

    def test_no_restore_flag(self) -> None:
        self.assertTrue(ide.parse_args(["--no-restore"]).no_restore)


class TestRecentCorpora(IdeTestCase):
    """Corpora are remembered the same way grammars and inputs are."""

    def setUp(self) -> None:
        super().setUp()
        self.app.grammar_pane.set_content(GRAMMAR)
        self.app.input_pane.set_content(INPUT)

    def entries(self, kind: str = ide.CORPUS_KIND) -> list[str]:
        menu = self.app.recent_menus[kind][0]
        return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) != "separator"]

    def write_corpus(self, name: str = "demo.corpus.json") -> Path:
        corpus = ide.Corpus()
        corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        path = self.tmp / name
        corpus.save(path)
        return path

    def test_the_corpus_menu_offers_a_recent_list(self) -> None:
        self.assertEqual(self.entries(), ["(empty)"])

    def test_opening_a_corpus_remembers_it(self) -> None:
        path = self.write_corpus()
        self.app.load_corpus(path, announce=False)
        self.app.update()
        self.assertEqual(self.app.settings.recent(ide.CORPUS_KIND), [str(path.resolve())])
        self.assertEqual(self.entries()[0], str(path.resolve()))

    def test_saving_a_corpus_remembers_it(self) -> None:
        self.app.corpus.add(ide.CorpusCase(name="ok", text=INPUT))
        path = self.tmp / "saved.corpus.json"
        self.app._write_corpus(path)
        self.app.update()
        self.assertEqual(self.entries()[0], str(path.resolve()))

    def test_a_corpus_adopted_beside_a_grammar_is_remembered(self) -> None:
        grammar = self.tmp / "demo.lark"
        grammar.write_text(GRAMMAR, encoding="utf-8")
        corpus = self.write_corpus("demo.corpus.json")
        self.app.open_recent(ide.GRAMMAR_KIND, grammar)
        self.app.update()
        self.assertEqual(self.app.settings.recent(ide.CORPUS_KIND), [str(corpus.resolve())])

    def test_opening_from_the_recent_list(self) -> None:
        path = self.write_corpus()
        self.app.open_recent(ide.CORPUS_KIND, path)
        self.app.update()
        self.assertEqual([case.name for case in self.app.corpus.cases], ["ok"])
        self.assertEqual(self.app.corpus.path, path)

    def test_the_newest_corpus_comes_first(self) -> None:
        first = self.write_corpus("one.corpus.json")
        second = self.write_corpus("two.corpus.json")
        self.app.load_corpus(first, announce=False)
        self.app.load_corpus(second, announce=False)
        self.app.update()
        self.assertEqual(self.entries()[:2], [str(second.resolve()), str(first.resolve())])

    def test_a_deleted_corpus_drops_out_of_the_list(self) -> None:
        path = self.write_corpus()
        self.app.load_corpus(path, announce=False)
        path.unlink()
        self.assertEqual(self.app.settings.recent(ide.CORPUS_KIND), [])

    def test_clearing_the_corpus_list(self) -> None:
        self.app.load_corpus(self.write_corpus(), announce=False)
        self.app.clear_recent(ide.CORPUS_KIND)
        self.app.update()
        self.assertEqual(self.entries(), ["(empty)"])

    def test_the_three_lists_stay_separate(self) -> None:
        grammar = self.tmp / "demo.lark"
        grammar.write_text(GRAMMAR, encoding="utf-8")
        corpus = self.write_corpus("separate.corpus.json")
        self.app.open_recent(ide.GRAMMAR_KIND, grammar)
        self.app.load_corpus(corpus, announce=False)
        self.assertEqual(self.app.settings.recent(ide.GRAMMAR_KIND), [str(grammar.resolve())])
        self.assertEqual(self.app.settings.recent(ide.CORPUS_KIND), [str(corpus.resolve())])
        self.assertEqual(self.app.settings.recent(ide.INPUT_KIND), [])

    def test_a_corpus_that_fails_to_load_is_not_remembered(self) -> None:
        path = self.tmp / "bad.corpus.json"
        path.write_text("not json", encoding="utf-8")
        self.assertFalse(self.app.load_corpus(path, announce=False))
        self.assertEqual(self.app.settings.recent(ide.CORPUS_KIND), [])


class TestCursorPosition(IdeTestCase):
    """Both editable panes report where the caret is, so a reported error can be found by hand."""

    def test_both_panes_start_at_line_one_column_one(self) -> None:
        for pane in (self.app.grammar_pane, self.app.input_pane):
            self.assertEqual(pane.position.cget("text"), "Ln 1, Col 1")

    def test_the_label_follows_the_caret(self) -> None:
        pane = self.app.input_pane
        pane.set_content("a = 1\nbb = 2\nccc = 3\n")
        pane.goto(3, 5)
        self.app.update()
        self.assertEqual(pane.position.cget("text"), "Ln 3, Col 5")

    def test_columns_match_what_lark_reports(self) -> None:
        """A parse error says 'line 2, column 5'; putting the caret there must agree."""
        self.parse(text="a = 1\nb = ?\n")
        self.assertIn("line 2, column 5", self.status())
        self.app.input_pane.goto(2, 5)
        self.app.update()
        self.assertEqual(self.app.input_pane.position.cget("text"), "Ln 2, Col 5")
        self.assertEqual(self.app.input_pane.text.get("insert"), "?")

    def test_a_selection_is_counted(self) -> None:
        pane = self.app.input_pane
        pane.set_content("abcdef\n")
        pane.text.tag_add("sel", "1.1", "1.4")
        pane._refresh_position()
        self.app.update()
        self.assertEqual(pane.position.cget("text"), "Ln 1, Col 1  (3 selected)")

    def test_typing_updates_the_label(self) -> None:
        """Guards against a later bind() for the same event silently replacing this one."""
        pane = self.app.input_pane
        pane.text.focus_set()
        for key in "hello":
            pane.text.event_generate("<KeyPress>", keysym=key)
            pane.text.event_generate("<KeyRelease>", keysym=key)
        self.app.update()
        self.assertEqual(pane.position.cget("text"), "Ln 1, Col 6")

    def test_loading_puts_the_caret_at_the_top(self) -> None:
        pane = self.app.input_pane
        pane.set_content("a = 1\nbb = 2\nccc = 3\n")
        self.app.update()
        self.assertEqual(pane.text.index("insert"), "1.0")
        self.assertEqual(pane.position.cget("text"), "Ln 1, Col 1")


class TestLineNumbers(IdeTestCase):
    def numbers(self, pane) -> list[str]:
        pane._draw_gutter()
        self.app.update_idletasks()
        return [pane.gutter.itemcget(i, "text") for i in pane.gutter.find_all()]

    def test_the_gutter_numbers_the_visible_lines(self) -> None:
        pane = self.app.grammar_pane
        pane.set_content("".join(f"rule{i}: NAME\n" for i in range(1, 12)))
        self.app.update()
        self.assertEqual(self.numbers(pane)[:4], ["1", "2", "3", "4"])

    def test_the_gutter_follows_the_scroll(self) -> None:
        pane = self.app.grammar_pane
        pane.set_content("".join(f"rule{i}: NAME\n" for i in range(1, 300)))
        self.app.update()
        pane.text.yview_moveto(0.5)
        self.app.update()
        first = int(self.numbers(pane)[0])
        self.assertGreater(first, 1, "the gutter should show the lines actually on screen")

    def test_the_gutter_widens_for_more_digits(self) -> None:
        pane = self.app.grammar_pane
        pane.set_content("a\n" * 5)
        self.app.update()
        self.numbers(pane)
        narrow = int(pane.gutter.cget("width"))
        pane.set_content("a\n" * 20000)
        self.app.update()
        self.numbers(pane)
        self.assertGreater(int(pane.gutter.cget("width")), narrow)

    def test_the_toggle_hides_and_shows_it(self) -> None:
        self.app.line_numbers.set(False)
        self.app.on_line_numbers_changed()
        self.app.update()
        for pane in (self.app.grammar_pane, self.app.input_pane):
            self.assertFalse(pane.gutter.winfo_ismapped())
        self.app.line_numbers.set(True)
        self.app.on_line_numbers_changed()
        self.app.update()
        self.assertTrue(self.app.grammar_pane.gutter.winfo_ismapped())

    def test_the_toggle_survives_a_restart(self) -> None:
        self.app.line_numbers.set(False)
        self.app.on_line_numbers_changed()
        self.app.destroy()
        self.app = self.new_app()
        self.assertFalse(self.app.line_numbers.get())

    def test_drawing_when_hidden_does_nothing(self) -> None:
        pane = self.app.grammar_pane
        pane.show_line_numbers(False)
        pane._draw_gutter()
        self.assertEqual(pane.gutter.find_all(), ())


class TestGrammarSearch(IdeTestCase):
    SAMPLE = 'start: pair+\npair: NAME "=" value\nvalue: NUMBER | NAME\nvalues: value+\nNAME: /[a-z]+/\n'

    def setUp(self) -> None:
        super().setUp()
        self.pane = self.app.grammar_pane
        self.pane.set_content(self.SAMPLE)
        self.app.update()

    def matched_text(self) -> list[str]:
        return [self.pane.text.get(a, b) for a, b in self.pane.match_ranges()]

    def test_only_the_grammar_pane_has_a_search_bar(self) -> None:
        self.assertTrue(self.pane.searchable)
        self.assertFalse(self.app.input_pane.searchable)
        self.assertEqual(self.app.input_pane.refresh_search(), 0)

    def test_searching_marks_every_occurrence(self) -> None:
        self.pane.set_search("NAME")
        self.app.update()
        self.assertEqual(self.matched_text(), ["NAME"] * 3)
        self.assertEqual(self.pane.search_count.cget("text"), "3 matches")

    def test_matching_is_whole_word(self) -> None:
        """'value' must not light up inside 'values', or the highlight is noise."""
        self.pane.set_search("value")
        self.app.update()
        self.assertEqual(len(self.matched_text()), 3)
        self.assertTrue(all(text == "value" for text in self.matched_text()))

    def test_one_match_is_singular(self) -> None:
        self.pane.set_search("values")
        self.app.update()
        self.assertEqual(self.pane.search_count.cget("text"), "1 match")

    def test_a_non_identifier_falls_back_to_a_plain_search(self) -> None:
        self.pane.set_search('"="')
        self.app.update()
        self.assertEqual(self.matched_text(), ['"="'])

    def test_selecting_an_identifier_becomes_the_search(self) -> None:
        self.pane.text.tag_add("sel", "2.0", "2.4")
        self.pane._on_selection()
        self.app.update()
        self.assertEqual(self.pane.search_term.get(), "pair")
        self.assertEqual(len(self.matched_text()), 2)

    def test_selecting_something_that_is_not_a_word_is_ignored(self) -> None:
        self.pane.set_search("NAME")
        self.pane.text.tag_add("sel", "1.0", "3.0")
        self.pane._on_selection()
        self.app.update()
        self.assertEqual(self.pane.search_term.get(), "NAME")

    def test_clearing_removes_every_mark(self) -> None:
        self.pane.set_search("NAME")
        self.pane.clear_search()
        self.app.update()
        self.assertEqual(self.matched_text(), [])
        self.assertEqual(self.pane.search_count.cget("text"), "")

    def test_find_next_walks_the_matches_and_wraps(self) -> None:
        self.pane.set_search("NAME")
        self.app.update()
        self.pane.text.mark_set("insert", "1.0")
        seen = []
        for _ in range(4):
            self.pane.find_next()
            seen.append(self.pane.text.index("insert"))
        self.assertEqual(len(set(seen)), 3, f"three matches, cycled: {seen}")
        self.assertEqual(seen[0], seen[3], "it should wrap back to the first")

    def test_find_next_with_no_matches(self) -> None:
        self.pane.set_search("nosuchrule")
        self.app.update()
        self.assertFalse(self.pane.find_next())

    def test_marks_survive_syntax_highlighting(self) -> None:
        """The highlighter clears its own tags; it must not clear the search marks."""
        self.pane.set_search("NAME")
        self.app.highlighter.highlight()
        self.app.update()
        self.assertEqual(len(self.matched_text()), 3)

    def test_marks_are_refreshed_after_an_edit(self) -> None:
        self.pane.set_search("NAME")
        self.pane.text.insert("1.0", "NAME NAME\n")
        self.app._run_highlight()
        self.app.update()
        self.assertEqual(len(self.matched_text()), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
