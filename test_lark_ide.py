#!/usr/bin/env python3
"""Tests for lark-ide.

Needs a display; run headless with:

    HOME=$(mktemp -d) xvfb-run -a python3 test_lark_ide.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

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


class IdeTestCase(unittest.TestCase):
    """Common setup: a real application window backed by a throwaway config directory."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.settings_path = self.tmp / "settings.json"
        self._original_settings_path = ide.SETTINGS_PATH
        ide.SETTINGS_PATH = self.settings_path
        self.app = self.new_app()

    def tearDown(self) -> None:
        ide.SETTINGS_PATH = self._original_settings_path
        try:
            self.app.destroy()
        except tk.TclError:
            pass

    def new_app(self) -> object:
        app = ide.LarkIde()
        app.update()
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
        self.assertEqual(len(saved["sashes"]), 2)

        self.app.destroy()
        self.app = self.new_app()
        menu = self.app.recent_menus[ide.GRAMMAR_KIND]
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
        self.assertEqual(self.app.recent_menus[ide.GRAMMAR_KIND].entrycget(0, "label"), "(empty)")

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
        pane.set_file_commands([("New Grammar", pane.new_file)])
        self.assertEqual(pane.menu.index("end"), before - 3)
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
        self.assertEqual(ide.run_corpus(self.parser(), cases), (1, 1))
        self.assertEqual(cases[0].result, ide.RESULT_PASS)

    def test_a_case_that_does_not_parse_fails(self) -> None:
        cases = [ide.CorpusCase(name="bad", text="a = ?")]
        self.assertEqual(ide.run_corpus(self.parser(), cases), (0, 1))
        self.assertEqual(cases[0].result, ide.RESULT_FAIL)
        self.assertTrue(cases[0].detail)

    def test_an_error_case_passes_when_it_fails_to_parse(self) -> None:
        cases = [ide.CorpusCase(name="bad", text="a = ?", expect=ide.EXPECT_ERROR)]
        self.assertEqual(ide.run_corpus(self.parser(), cases), (1, 1))

    def test_an_error_case_fails_when_it_parses(self) -> None:
        cases = [ide.CorpusCase(name="fine", text=INPUT, expect=ide.EXPECT_ERROR)]
        self.assertEqual(ide.run_corpus(self.parser(), cases), (0, 1))
        self.assertIn("error was expected", cases[0].detail)

    def test_a_recorded_tree_is_compared(self) -> None:
        parser = self.parser()
        recorded = parser.parse(INPUT).pretty().rstrip("\n")
        good = [ide.CorpusCase(name="ok", text=INPUT, tree=recorded)]
        self.assertEqual(ide.run_corpus(parser, good), (1, 1))

        stale = [ide.CorpusCase(name="stale", text=INPUT, tree="start\n  something_else")]
        self.assertEqual(ide.run_corpus(parser, stale), (0, 1))
        self.assertIn("differs", stale[0].detail)

    def test_a_grammar_change_that_keeps_parsing_still_fails_a_recorded_case(self) -> None:
        """The regression the recorded tree exists to catch: it still parses, but differently."""
        parser = self.parser()
        recorded = parser.parse("a = 1\n").pretty().rstrip("\n")
        renamed = GRAMMAR.replace("pair:", "binding:").replace("start: pair+", "start: binding+")
        cases = [ide.CorpusCase(name="ok", text="a = 1\n", tree=recorded)]
        self.assertEqual(ide.run_corpus(self.parser(renamed), cases), (0, 1))


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
        self.assertEqual([row[2] for row in rows], [ide.RESULT_PASS, ide.RESULT_FAIL])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
