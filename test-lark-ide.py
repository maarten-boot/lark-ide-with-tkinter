#!/usr/bin/env python3
"""Tests for lark-ide.

Needs a display; run headless with:

    HOME=$(mktemp -d) xvfb-run -a python3 test_lark_ide.py
"""

from __future__ import annotations

import importlib.util
import json
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

    def test_lalr_parser_is_usable(self) -> None:
        self.app.parser_name.set("lalr")
        self.app.on_parser_changed()
        self.parse()
        self.assertIn("Parsed OK", self.status())
        self.assertIn("lalr", self.app.status.detail.cget("text"))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
