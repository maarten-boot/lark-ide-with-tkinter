#!/usr/bin/env python3
"""Railroad diagrams for Lark grammars, emitted as a standalone SVG document.

The grammar is parsed with lark's own `lark.lark` grammar, so the structure comes from lark rather
than from guessing at the text. Each rule becomes a diagram; the whole grammar becomes one document.

Layout follows the usual railroad approach: every item reports a width and how far it reaches above
and below the line running through it, sizes are gathered bottom up, and positions are handed down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape

import lark

ARC = 10.0
V_GAP = 10.0
H_GAP = 10.0
CHAR_WIDTH = 7.3
BOX_HEIGHT = 24.0
BOX_PAD = 12.0
MIN_BOX = 34.0
TITLE_HEIGHT = 30.0
DIAGRAM_GAP = 26.0
MARGIN = 16.0
MAX_LABEL = 40

STYLE = """
  .rr-frame { fill: none; stroke: none; }
  .rr-line { fill: none; stroke: #444; stroke-width: 1.6; }
  .rr-rule { fill: #eef3fb; stroke: #4078f2; stroke-width: 1.4; }
  .rr-term { fill: #f3f7ee; stroke: #50a14f; stroke-width: 1.4; }
  .rr-text { font-family: ui-monospace, "DejaVu Sans Mono", monospace; font-size: 13px; fill: #222;
             text-anchor: middle; dominant-baseline: central; }
  .rr-title { font-family: ui-monospace, "DejaVu Sans Mono", monospace; font-size: 15px;
              font-weight: bold; fill: #111; }
  .rr-note { font-family: ui-monospace, "DejaVu Sans Mono", monospace; font-size: 11px; fill: #888;
             text-anchor: middle; }
"""


def _line(x1: float, y1: float, x2: float, y2: float) -> str:
    return f'<path class="rr-line" d="M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}"/>'


def _branch_out(x: float, y: float, drop: float) -> str:
    """From (x, y) heading right, down to (x + 2*ARC, y + drop), heading right again."""
    return (
        f'<path class="rr-line" d="M {x:.1f} {y:.1f} a {ARC} {ARC} 0 0 1 {ARC} {ARC} '
        f'v {drop - 2 * ARC:.1f} a {ARC} {ARC} 0 0 0 {ARC} {ARC}"/>'
    )


def _branch_in(x: float, y: float, rise: float) -> str:
    """From (x, y) heading right, up to (x + 2*ARC, y - rise), heading right again."""
    return (
        f'<path class="rr-line" d="M {x:.1f} {y:.1f} a {ARC} {ARC} 0 0 0 {ARC} {-ARC} '
        f'v {-(rise - 2 * ARC):.1f} a {ARC} {ARC} 0 0 1 {ARC} {-ARC}"/>'
    )


class Item:
    """One piece of a diagram. Sizes are known after construction; drawing needs a position."""

    width: float = 0.0
    up: float = 0.0
    down: float = 0.0

    def draw(self, x: float, y: float, out: list[str]) -> float:
        raise NotImplementedError


@dataclass
class Leaf(Item):
    """A terminal or a reference to another rule, drawn as a labelled box."""

    text: str
    is_rule: bool

    def __post_init__(self) -> None:
        label = self.text if len(self.text) <= MAX_LABEL else self.text[: MAX_LABEL - 1] + "\u2026"
        self.label = label
        self.width = max(len(label) * CHAR_WIDTH + 2 * BOX_PAD, MIN_BOX)
        self.up = BOX_HEIGHT / 2
        self.down = BOX_HEIGHT / 2

    def draw(self, x: float, y: float, out: list[str]) -> float:
        style = "rr-rule" if self.is_rule else "rr-term"
        radius = 4 if self.is_rule else BOX_HEIGHT / 2
        out.append(
            f'<rect class="{style}" x="{x:.1f}" y="{y - BOX_HEIGHT / 2:.1f}" '
            f'width="{self.width:.1f}" height="{BOX_HEIGHT:.1f}" rx="{radius:.1f}"/>'
        )
        out.append(f'<text class="rr-text" x="{x + self.width / 2:.1f}" y="{y:.1f}">{escape(self.label)}</text>')
        return x + self.width


@dataclass
class Skip(Item):
    """The empty path an optional construct takes when it matches nothing."""

    def __post_init__(self) -> None:
        self.width = MIN_BOX
        self.up = BOX_HEIGHT / 2
        self.down = BOX_HEIGHT / 2

    def draw(self, x: float, y: float, out: list[str]) -> float:
        out.append(_line(x, y, x + self.width, y))
        return x + self.width


@dataclass
class Sequence(Item):
    items: list[Item]

    def __post_init__(self) -> None:
        self.width = sum(item.width for item in self.items) + H_GAP * max(len(self.items) - 1, 0)
        self.up = max((item.up for item in self.items), default=BOX_HEIGHT / 2)
        self.down = max((item.down for item in self.items), default=BOX_HEIGHT / 2)

    def draw(self, x: float, y: float, out: list[str]) -> float:
        for index, item in enumerate(self.items):
            if index:
                out.append(_line(x, y, x + H_GAP, y))
                x += H_GAP
            x = item.draw(x, y, out)
        return x


@dataclass
class Choice(Item):
    """Alternatives. The first runs along the main line; the rest hang below it."""

    items: list[Item]
    offsets: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.inner = max(item.width for item in self.items)
        self.width = self.inner + 4 * ARC
        first = self.items[0]
        self.up = first.up
        depth = max(first.down, ARC)
        self.offsets = [0.0]
        for item in self.items[1:]:
            depth += V_GAP + item.up
            self.offsets.append(depth)
            depth += item.down
        self.down = depth

    def draw(self, x: float, y: float, out: list[str]) -> float:
        inner_x = x + 2 * ARC
        right = x + self.width
        first = self.items[0]
        start = inner_x + (self.inner - first.width) / 2
        out.append(_line(x, y, start, y))
        end = first.draw(start, y, out)
        out.append(_line(end, y, right, y))
        for item, drop in zip(self.items[1:], self.offsets[1:]):
            out.append(_branch_out(x, y, drop))
            out.append(_branch_in(inner_x + self.inner, y + drop, drop))
            start = inner_x + (self.inner - item.width) / 2
            out.append(_line(inner_x, y + drop, start, y + drop))
            end = item.draw(start, y + drop, out)
            out.append(_line(end, y + drop, inner_x + self.inner, y + drop))
        return right


@dataclass
class Repeat(Item):
    """One or more of an item, with a loop back underneath and an optional count label."""

    item: Item
    label: str = ""

    def __post_init__(self) -> None:
        self.width = max(self.item.width + 2 * ARC, 4 * ARC)
        self.up = self.item.up
        self.loop_drop = self.item.down + V_GAP + ARC
        self.down = self.loop_drop + (14.0 if self.label else 4.0)

    def draw(self, x: float, y: float, out: list[str]) -> float:
        inner_x = x + ARC
        right = x + self.width
        out.append(_line(x, y, inner_x, y))
        end = self.item.draw(inner_x, y, out)
        out.append(_line(end, y, right, y))

        drop = self.loop_drop
        span = max(end - inner_x - 2 * ARC, 0.0)
        out.append(
            f'<path class="rr-line" d="M {end:.1f} {y:.1f} a {ARC} {ARC} 0 0 1 {ARC} {ARC} '
            f"v {drop - 2 * ARC:.1f} a {ARC} {ARC} 0 0 1 {-ARC} {ARC} h {-span:.1f} "
            f"a {ARC} {ARC} 0 0 1 {-ARC} {-ARC} v {-(drop - 2 * ARC):.1f} "
            f'a {ARC} {ARC} 0 0 1 {ARC} {-ARC}"/>'
        )
        if self.label:
            out.append(
                f'<text class="rr-note" x="{(inner_x + end) / 2:.1f}" y="{y + drop + 12:.1f}">'
                f"{escape(self.label)}</text>"
            )
        return right


def optional(item: Item) -> Item:
    return Choice([item, Skip()])


# -- turning a lark grammar into items -----------------------------------------------------------

_META_PARSER: lark.Lark | None = None


def _meta_parser() -> lark.Lark:
    global _META_PARSER
    if _META_PARSER is None:
        # maybe_placeholders would insert None children for optional parts, which are not real operands
        _META_PARSER = lark.Lark.open_from_package(
            "lark", "grammars/lark.lark", parser="lalr", maybe_placeholders=False
        )
    return _META_PARSER


def _token_text(node: object) -> str:
    children = getattr(node, "children", None)
    return str(children[0]) if children else str(node)


def _convert(node: object) -> Item:
    """Map one node of lark's own grammar tree onto a diagram item."""
    data = getattr(node, "data", None)
    if data is None:
        return Leaf(str(node), is_rule=str(node)[:1].islower())
    children = [child for child in node.children if child is not None]

    if data == "expansions":
        return Choice([_convert(child) for child in children])
    if data == "expansion":
        if len(children) == 1:
            return _convert(children[0])
        return Sequence([_convert(child) for child in children])
    if data == "alias":
        return _convert(children[0])
    if data == "maybe":
        return optional(_convert(children[0]) if len(children) == 1 else Sequence([_convert(c) for c in children]))
    if data == "expr":
        return _convert_expr(children)
    if data == "name":
        text = _token_text(node)
        return Leaf(text, is_rule=text[:1].islower() or text[:1] == "_" and text[1:2].islower())
    if data == "literal":
        return Leaf(_token_text(node), is_rule=False)
    if data == "literal_range":
        return Leaf(" .. ".join(str(child) for child in children), is_rule=False)
    if data == "template_usage":
        return Leaf(f"{_token_text(children[0])}{{...}}", is_rule=True)
    if data == "value":
        return _convert(children[0])
    return Sequence([_convert(child) for child in children]) if children else Skip()


def _convert_expr(children: list) -> Item:
    item = _convert(children[0])
    operands = [str(child) for child in children[1:] if child is not None]
    if operands == ["?"]:
        return optional(item)
    if operands == ["*"]:
        return optional(Repeat(item))
    if operands == ["+"]:
        return Repeat(item)
    if len(operands) == 1:
        return Repeat(item, label=f"exactly {operands[0]}")
    if len(operands) == 2:
        return Repeat(item, label=f"{operands[0]} to {operands[1]}")
    return item


def grammar_items(grammar_text: str) -> list[tuple[str, Item]]:
    """Every rule and terminal definition in the grammar, in source order, as diagram items."""
    tree = _meta_parser().parse(grammar_text)
    found = []
    for statement in tree.children:
        data = getattr(statement, "data", None)
        if data not in ("rule", "token"):
            continue
        name = str(statement.children[0])
        definition = statement.children[-1]
        found.append((name, _convert(definition)))
    return found


# -- the document ---------------------------------------------------------------------------------


def _diagram(name: str, item: Item, top: float, width: float, out: list[str]) -> float:
    """Draw one titled diagram with its line starting at the left margin. Returns the next free y."""
    out.append(f'<text class="rr-title" x="{MARGIN:.1f}" y="{top + 14:.1f}">{escape(name)}</text>')
    y = top + TITLE_HEIGHT + item.up
    out.append(_line(MARGIN, y, MARGIN + ARC, y))
    end = item.draw(MARGIN + ARC, y, out)
    out.append(_line(end, y, width - MARGIN, y))
    out.append(f'<circle class="rr-line" cx="{MARGIN:.1f}" cy="{y:.1f}" r="3"/>')
    out.append(f'<circle class="rr-line" cx="{width - MARGIN:.1f}" cy="{y:.1f}" r="3"/>')
    return y + item.down + DIAGRAM_GAP


def svg_document(entries: list[tuple[str, Item]]) -> str:
    """A standalone SVG holding every diagram, stacked with the rule name above each."""
    if not entries:
        entries = [("(no rules)", Skip())]
    width = max(item.width for _name, item in entries) + 2 * ARC + 2 * MARGIN
    body: list[str] = []
    y = MARGIN
    for name, item in entries:
        y = _diagram(name, item, y, width, body)
    height = y - DIAGRAM_GAP + MARGIN
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}">'
        ),
        f"<style>{STYLE}</style>",
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#ffffff"/>',
        *body,
        "</svg>",
    ]
    return "\n".join(parts) + "\n"


def grammar_svg(grammar_text: str) -> str:
    """The whole grammar as one SVG document."""
    return svg_document(grammar_items(grammar_text))
