"""Move rewrites resolve Markdown hrefs after exactly one URL decode.

Use the real rewriter and FULL_NOTE/frontmatter parser under conftest's
hermetic settings; no database, HTTP client, or registered vault is used.
Generated destinations retain the existing raw-space output contract.
"""

import pytest

from src.mcp_server import tools
from src.services.links import build_vault_index, extract_links, resolve_target


OLD = "fixture/original/Target Space.md"
NEW = "fixture/destination/Moved Space.md"
SOURCE = "fixture/incoming/Source.md"


def _index(*extra):
    return build_vault_index([(OLD, 1), *extra])


@pytest.mark.parametrize("encoded", ["%20", " "])
def test_incoming_href_preserves_label_anchor_and_wiki_alias(encoded):
    body = (
        f"[the `foo` option](../original/Target{encoded}Space.md#A%23B%25)\n"
        "[[fixture/original/Target Space#Details|kept `alias`]]\n"
    )
    index = _index(("fixture/other/Target Space.md", 2))
    assert [resolve_target(link.target, SOURCE, index) for link in extract_links(body)] == [1, 1]

    assert tools._rewrite_links_in_text(body, OLD, NEW, SOURCE, index) == (
        "[the `foo` option](../destination/Moved Space.md#A%23B%25)\n"
        "[[fixture/destination/Moved Space#Details|kept `alias`]]\n",
        2,
    )


def test_encoded_selflink_uses_moved_source_location_for_output():
    body = "[self](./Target%20Space.md#Details%23A)\n"
    assert tools._rewrite_links_in_text(
        body, OLD, NEW, OLD, _index(), output_source_path=NEW
    ) == ("[self](Moved Space.md#Details%23A)\n", 1)


@pytest.mark.parametrize("move_literal_percent", [False, True])
def test_href_is_not_double_decoded_when_both_notes_exist(move_literal_percent):
    literal = "fixture/original/Target%20Space.md"
    index = _index((literal, 2))
    body = "[literal](../original/Target%2520Space.md#A%23B%25)\n"
    (link,) = extract_links(body)
    assert link.target == "../original/Target%20Space"
    assert resolve_target(link.target, SOURCE, index) == 2
    moved = literal if move_literal_percent else OLD
    expected = "[literal](../destination/Moved Space.md#A%23B%25)\n" if move_literal_percent else body
    assert tools._rewrite_links_in_text(body, moved, NEW, SOURCE, index) == (
        expected, int(move_literal_percent)
    )


@pytest.mark.parametrize("encoded", ["%23", "%7C", "%7c"])
@pytest.mark.parametrize("move_literal_delimiter", [False, True])
def test_decoded_path_delimiter_never_rewrites_a_different_note(encoded, move_literal_delimiter):
    delimiter = "#" if encoded == "%23" else "|"
    truncated = "fixture/original/Target.md"
    literal = f"fixture/original/Target{delimiter}Extra.md"
    index = build_vault_index([(truncated, 1), (literal, 2)])
    body = f"[keep](../original/Target{encoded}Extra.md#Details)\n"
    # Existing index ambiguity: the wiki-style resolver strips decoded #/|.
    # Conservative rewriting leaves the href alone for either moved note.
    (link,) = extract_links(body)
    assert resolve_target(link.target, SOURCE, index) == 1
    moved = literal if move_literal_delimiter else truncated
    assert tools._rewrite_links_in_text(body, moved, NEW, SOURCE, index) == (body, 0)


@pytest.mark.parametrize("href,label", [
    ("../original/Target%00Space.md", "keep"),
    ("../original/Target%20Space.md", "keep\x00label"),
])
def test_nul_in_decoded_href_or_label_is_not_rewritten(href, label):
    body = f"[{label}]({href}#Details)\n"
    assert extract_links(body) == []
    assert tools._rewrite_links_in_text(body, OLD, NEW, SOURCE, _index()) == (body, 0)


def test_unrelated_same_basename_keeps_original_bytes():
    body = "[[Target Space#Details|local alias]]\n[local](./Target%20Space.md#A%25)\n"
    source = "fixture/other/Source.md"
    index = _index(("fixture/other/Target Space.md", 2))
    assert [resolve_target(link.target, source, index) for link in extract_links(body)] == [2, 2]
    assert tools._rewrite_links_in_text(body, OLD, NEW, source, index) == (body, 0)


def test_full_note_keeps_frontmatter_fences_inline_code_and_surrounding_bytes():
    protected = (
        "---\n"
        'title: "Synthetic source"\n'
        "example: |\n  ```\n  synthetic metadata\n"
        "---\n"
        "```md\n[keep](../original/Target%20Space.md)\n```\n"
        "`[keep](../original/Target%20Space.md)`\n"
    )
    link = "[the `foo` option](../original/Target%20Space.md#Details%25)"
    body = protected + "Before π " + link + " after.\n"
    expected = protected + "Before π " + "[the `foo` option](../destination/Moved Space.md#Details%25)" + " after.\n"
    assert tools._rewrite_links_in_text(body, OLD, NEW, SOURCE, _index()) == (expected, 1)
