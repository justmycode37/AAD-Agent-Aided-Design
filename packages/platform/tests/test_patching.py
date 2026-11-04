import pytest

from aiad_platform.daemon.patching import PatchError, apply_patch

SOURCE = "a\nb\nc\nd\n"


def test_applies_a_unified_diff():
    patch = "--- a/model.py\n+++ b/model.py\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
    assert apply_patch(SOURCE, patch) == "a\nB\nc\nd\n"


def test_pure_insertion_after_a_line():
    assert apply_patch(SOURCE, "@@ -2,0 +3 @@\n+x\n") == "a\nb\nx\nc\nd\n"


def test_two_hunks():
    patch = "@@ -1 +1 @@\n-a\n+A\n@@ -4 +4 @@\n-d\n+D\n"
    assert apply_patch(SOURCE, patch) == "A\nb\nc\nD\n"


def test_stale_context_fails_instead_of_guessing():
    with pytest.raises(PatchError, match="does not match"):
        apply_patch(SOURCE, "@@ -1,2 +1,2 @@\n a\n-z\n+Z\n")


def test_no_hunks_fails():
    with pytest.raises(PatchError, match="no hunks"):
        apply_patch(SOURCE, "not a diff")


def test_header_like_source_lines_are_applied():
    assert apply_patch("-- old\n", "@@ -1 +1 @@\n--- old\n+++ new\n") == "++ new\n"


def test_untouched_eof_without_newline_is_preserved():
    assert apply_patch("a\nb", "@@ -1 +1 @@\n-a\n+A\n") == "A\nb"


def test_no_newline_marker_changes_eof():
    patch = "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n"
    assert apply_patch("old", patch) == "new"


@pytest.mark.parametrize("patch", [
    "@@ -1,2 +1,2 @@\n-a\n+A\n",
    "@@ -1 +1 @@\n-a\n+A\n+extra\n",
    "@@ -1 +7 @@\n-a\n+A\n",
    "@@ -1 +1 @@\n-a\n+A\ndiff --git a/other b/other\n--- a/other\n+++ b/other\n",
])
def test_inconsistent_or_multifile_hunks_fail(patch):
    with pytest.raises(PatchError):
        apply_patch(SOURCE, patch)
