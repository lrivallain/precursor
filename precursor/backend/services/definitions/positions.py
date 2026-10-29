"""Place the check's findings in a file's text, so an editor can mark them.

A finding names where it applies as a path through the YAML, such as
``steps[brief].agent``: keys joined by dots, and a list item by its step
``key`` (or its index when it has none), the way the loader writes them. That
path is walked over the composed YAML nodes, which keep their positions. When
part of it doesn't exist in the file (a required key that is missing, say), the
nearest part that does is used, so the finding still lands close by.

Positions are 1-based and end-exclusive, in Monaco's terms: lines split on
``\\r\\n``, ``\\r`` or ``\\n``, and columns count UTF-16 code units.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

import yaml

from precursor.backend.schemas.definitions_api import DefinitionIssue
from precursor.backend.services.definitions.loader import yaml_error_mark

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_SEGMENT = re.compile(r"([^.\[\]]*)((?:\[[^\[\]]*\])*)")
_LABEL = re.compile(r"\[([^\[\]]*)\]")


@dataclass(frozen=True)
class Span:
    line: int
    column: int
    end_line: int
    end_column: int


class _Text:
    """Index → (line, column) for one text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.starts = [0, *(m.end() for m in _LINE_BREAK.finditer(text))]

    def position(self, index: int) -> tuple[int, int]:
        index = max(0, min(index, len(self.text)))
        line = bisect.bisect_right(self.starts, index) - 1
        start = self.starts[line]
        column = len(self.text[start:index].encode("utf-16-le")) // 2
        return line + 1, column + 1

    def span(self, start: int, end: int) -> Span:
        line, column = self.position(start)
        end_line, end_column = self.position(max(end, start))
        if (end_line, end_column) == (line, column):
            # Zero-width: widen to one character so the mark is visible.
            end_column = column + 1
        return Span(line, column, end_line, end_column)

    def node(self, node: yaml.Node) -> Span:
        return self.span(node.start_mark.index, node.end_mark.index)


def _path(location: str) -> list[tuple[str, str]] | None:
    """``steps[brief].agent`` → ``[("key", "steps"), ("item", "brief"), ("key", "agent")]``."""
    steps: list[tuple[str, str]] = []
    for part in location.split("."):
        match = _SEGMENT.fullmatch(part)
        if match is None:
            return None
        name, labels = match.groups()
        if name:
            steps.append(("key", name))
        steps += [("item", label) for label in _LABEL.findall(labels)]
    return steps


def _entry(node: yaml.MappingNode, name: str) -> tuple[yaml.Node, yaml.Node] | None:
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and key.value == name:
            return key, value
    return None


def _item(node: yaml.SequenceNode, label: str) -> yaml.Node | None:
    # A step is named by its ``key`` when it has one, like the loader does.
    for item in node.value:
        if isinstance(item, yaml.MappingNode):
            entry = _entry(item, "key")
            if entry is not None and getattr(entry[1], "value", None) == label:
                return item
    if label.isdigit() and int(label) < len(node.value):
        indexed: yaml.Node = node.value[int(label)]
        return indexed
    return None


def _item_span(text: _Text, item: yaml.Node) -> Span:
    """A list item: its ``key`` value for a step, else its first key."""
    if isinstance(item, yaml.MappingNode) and item.value:
        entry = _entry(item, "key")
        return text.node(entry[1] if entry is not None else item.value[0][0])
    if isinstance(item, yaml.ScalarNode):
        return text.node(item)
    return text.span(item.start_mark.index, item.start_mark.index)


def _locate(text: _Text, root: yaml.Node, location: str | None) -> Span:
    if isinstance(root, yaml.MappingNode) and root.value:
        best = text.node(root.value[0][0])
    else:
        best = text.span(root.start_mark.index, root.start_mark.index)
    node = root
    for kind, name in _path(location or "") or []:
        if kind == "key":
            if not isinstance(node, yaml.MappingNode):
                break
            entry = _entry(node, name)
            if entry is None:
                break
            key, node = entry
            # A value on the same line is what's wrong; a block (or nothing)
            # is marked at its key.
            scalar = isinstance(node, yaml.ScalarNode) and (
                node.start_mark.index != node.end_mark.index
            )
            best = text.node(node if scalar else key)
        else:
            if not isinstance(node, yaml.SequenceNode):
                break
            found = _item(node, name)
            if found is None:
                break
            node = found
            best = _item_span(text, node)
    return best


def with_positions(content: str, issues: list[DefinitionIssue]) -> list[DefinitionIssue]:
    """``issues`` with their line and column in ``content`` filled in."""
    text = _Text(content)
    error = yaml_error_mark(content)
    span: Span | None = None
    root: yaml.Node | None = None
    if error is not None:
        # Nothing else can be located in a file that doesn't load.
        span = text.span(error.index, error.index)
    else:
        try:
            root = yaml.compose(content, Loader=yaml.SafeLoader)
        except yaml.YAMLError:
            return issues
        if root is None:
            # An empty file: its findings go on the first line.
            span = text.span(0, 0)
    placed: list[DefinitionIssue] = []
    for issue in issues:
        at = span if root is None else _locate(text, root, issue.location)
        if at is None:
            placed.append(issue)
            continue
        placed.append(
            issue.model_copy(
                update={
                    "line": at.line,
                    "column": at.column,
                    "end_line": at.end_line,
                    "end_column": at.end_column,
                }
            )
        )
    return placed
