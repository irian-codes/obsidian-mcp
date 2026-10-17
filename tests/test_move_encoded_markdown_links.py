"""Move rewrites resolve Markdown hrefs after exactly one URL decode.

Use the real rewriter and FULL_NOTE/frontmatter parser under conftest's
hermetic settings; no database, HTTP client, or registered vault is used.
Generated relative destinations are URI-encoded without changing file identity.
"""

from types import SimpleNamespace
from urllib.parse import quote

import pytest

from src.auth.session import current_user_id
from src.mcp_server import tools
from src.mcp_server.auth import current_permission
from src.services import vault as vault_service
from src.services.tool_outcomes import BodyOutcome
from src.services.links import (
    MDLINK_HREF_MAX, build_vault_index, extract_links, resolve_target, scan_md_links,
)


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
        "[the `foo` option](../destination/Moved%20Space.md#A%23B%25)\n"
        "[[fixture/destination/Moved Space#Details|kept `alias`]]\n",
        2,
    )


def test_encoded_selflink_uses_moved_source_location_for_output():
    body = "[self](./Target%20Space.md#Details%23A)\n"
    assert tools._rewrite_links_in_text(
        body, OLD, NEW, OLD, _index(), output_source_path=NEW
    ) == ("[self](Moved%20Space.md#Details%23A)\n", 1)


@pytest.mark.parametrize("move_literal_percent", [False, True])
def test_href_is_not_double_decoded_when_both_notes_exist(move_literal_percent):
    literal = "fixture/original/Target%20Space.md"
    index = _index((literal, 2))
    body = "[literal](../original/Target%2520Space.md#A%23B%25)\n"
    (link,) = extract_links(body)
    assert link.target == "../original/Target%20Space"
    assert resolve_target(link.target, SOURCE, index) == 2
    moved = literal if move_literal_percent else OLD
    expected = "[literal](../destination/Moved%20Space.md#A%23B%25)\n" if move_literal_percent else body
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
    expected = protected + "Before π " + "[the `foo` option](../destination/Moved%20Space.md#Details%25)" + " after.\n"
    assert tools._rewrite_links_in_text(body, OLD, NEW, SOURCE, _index()) == (expected, 1)


@pytest.mark.parametrize("filename,encoded_filename,sibling", [
    ("Moved%20Space.md", "Moved%2520Space.md", "Moved Space.md"),
    ("Moved (café).md", "Moved%20%28caf%C3%A9%29.md", "Other.md"),
])
def test_generated_href_encodes_destination_and_resolves_exact_note(
    filename, encoded_filename, sibling
):
    destination = f"fixture/destination space/{filename}"
    body = "[the `foo` option](../original/Target%20Space.md#A%23B%25)\n"
    rewritten, count = tools._rewrite_links_in_text(
        body, OLD, destination, SOURCE, _index()
    )
    assert (rewritten, count) == (
        f"[the `foo` option](../destination%20space/{encoded_filename}#A%23B%25)\n",
        1,
    )
    post_move_index = build_vault_index([
        (destination, 1), (f"fixture/destination space/{sibling}", 2),
    ])
    (link,) = extract_links(rewritten)
    assert resolve_target(link.target, SOURCE, post_move_index) == 1


def test_rewrite_preserves_unrelated_link_bytes_beside_encoded_destination():
    unrelated = "[keep](../other/Target%20Space.md#Unchanged%25)\n"
    body = "[move](../original/Target%20Space.md#Details)\n" + unrelated
    index = _index(("fixture/other/Target Space.md", 2))
    assert tools._rewrite_links_in_text(body, OLD, NEW, SOURCE, index) == (
        "[move](../destination/Moved%20Space.md#Details)\n" + unrelated,
        1,
    )


def _boundary_destination(extra=0):
    # Each component fits the filesystem's 255-byte limit. The encoded path
    # before `.md` is 2,048 characters, plus `extra` ASCII characters.
    return "/".join(["文" * 80, "文" * 80, "文" * 66 + "a" * (6 + extra), "Moved.md"])


@pytest.mark.parametrize("anchor", ["", "#" + "A" * 100])
@pytest.mark.parametrize("extra", [0, 1])
def test_generated_href_checks_exact_scanner_boundary(extra, anchor):
    destination = _boundary_destination(extra)
    encoded = quote(destination, safe="/")
    assert len(encoded) - len(".md") == MDLINK_HREF_MAX + extra
    assert all(len(part.encode("utf-8")) <= 255 for part in destination.split("/"))
    candidate = f"[move]({encoded}{anchor})\n"
    # Pin the real scanner's boundary independently of the guard, with and
    # without an anchor: neither `.md` nor the anchor consumes its budget.
    assert len(list(scan_md_links(candidate))) == 1 - extra
    index = build_vault_index([("Old.md", 1)])
    if extra:
        with pytest.raises(tools.MoveRewriteHrefTooLong) as excinfo:
            tools._rewrite_links_in_text(
                f"[move](Old.md{anchor})\n", "Old.md", destination, "src.md", index
            )
        assert excinfo.value.source_path == "src.md"
        assert excinfo.value.count == MDLINK_HREF_MAX + 1
        assert excinfo.value.cap == MDLINK_HREF_MAX
    else:
        assert tools._rewrite_links_in_text(
            f"[move](Old.md{anchor})\n", "Old.md", destination, "src.md", index
        ) == (candidate, 1)
        (link,) = extract_links(candidate)
        assert resolve_target(
            link.target, "src.md", build_vault_index([(destination, 1)])
        ) == 1


@pytest.mark.parametrize("destination", [
    _boundary_destination(1),
    "/".join(["文" * 80] * 3 + ["Moved.md"]),
])
async def test_oversized_generated_href_aborts_before_filesystem_or_database_mutation(
    monkeypatch, tmp_path, destination
):
    # A valid self rewrite is planned before the oversized incoming rewrite.
    # Refusing only that source after committing would already be too late.
    originals = {"Old.md": b"[self](Old.md#Self)\n", "src.md": b"[move](Old.md#Incoming)\n"}
    for path, content in originals.items():
        (tmp_path / path).write_bytes(content)
    monkeypatch.setattr(vault_service.settings, "vault_path", str(tmp_path))
    statements = []
    commits = []
    planned_sources = []
    mutation_calls = []
    rows = [
        [SimpleNamespace(file_path="Old.md", id=1), SimpleNamespace(file_path="src.md", id=2)],
        [SimpleNamespace(file_path="src.md")],
        [],  # no stale extraction versions
    ]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            statements.append(statement)
            assert statement.is_select, "preflight must not mutate the database"
            i = len(statements) - 1
            return SimpleNamespace(all=lambda: rows[i])

        async def commit(self):
            commits.append(True)

    async def noop(*args, **kwargs):
        return None

    def forbidden_mutation(*args, **kwargs):
        mutation_calls.append(True)
        raise AssertionError("preflight must not rename or rewrite anything")

    real_rewrite = tools._rewrite_links_in_text

    def rewrite(*args, **kwargs):
        rewritten = real_rewrite(*args, **kwargs)
        planned_sources.append((args[3], rewritten[1]))
        return rewritten

    monkeypatch.setattr(tools, "async_session", Session)
    monkeypatch.setattr(tools, "_log_usage", noop)
    monkeypatch.setattr(tools, "_rewrite_links_in_text", rewrite)
    monkeypatch.setattr(tools, "move_file_no_clobber", forbidden_mutation)
    monkeypatch.setattr(tools, "write_file_at", forbidden_mutation)
    permission = current_permission.set("readwrite")
    user = current_user_id.set(None)
    try:
        result = await tools.move_note_impl("Old.md", destination, rewrite_links=True)
    finally:
        current_user_id.reset(user)
        current_permission.reset(permission)

    assert isinstance(result, BodyOutcome)
    assert result.refusal.code == "resource_limit"
    assert result.disposition == "refused"
    assert "Move aborted" in result
    assert "src.md" in result
    assert f"MDLINK_HREF_MAX={MDLINK_HREF_MAX}" in result
    assert "Nothing was moved, rewritten or reindexed" in result
    assert planned_sources == [("Old.md", 1)]
    assert len(statements) == 3 and all(statement.is_select for statement in statements)
    assert commits == []
    assert mutation_calls == []
    # Include directories, so even early creation of the destination's missing
    # parents fails this assertion, alongside any note rename/content change.
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == sorted(originals)
    assert {path: (tmp_path / path).read_bytes() for path in originals} == originals
