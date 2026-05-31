from datetime import datetime

from core.metadata import (
    _extract_json_object,
    as_list,
    first_nonempty,
    fmt_ts,
    looks_like_meeting,
    recorded_date,
    safe_filename,
    strip_frontmatter,
    strip_markdown_fence,
)


# --- fmt_ts ---------------------------------------------------------------


def test_fmt_ts_zero() -> None:
    assert fmt_ts(0) == "00:00:00"


def test_fmt_ts_one_hour_one_minute_one_second() -> None:
    assert fmt_ts(3661000) == "01:01:01"


def test_fmt_ts_negative_clamps_to_zero() -> None:
    assert fmt_ts(-5000) == "00:00:00"


def test_fmt_ts_sub_second_truncates() -> None:
    assert fmt_ts(999) == "00:00:00"
    assert fmt_ts(1000) == "00:00:01"


# --- safe_filename --------------------------------------------------------


def test_safe_filename_strips_forbidden_chars() -> None:
    cleaned = safe_filename('a/b:c*d?e"f<g>h|i#j[k]l')
    for ch in '\\/:*?"<>|#[]':
        assert ch not in cleaned


def test_safe_filename_collapses_whitespace() -> None:
    assert safe_filename("  hello    world  ") == "hello world"
    assert safe_filename("line1\nline2\rline3") == "line1 line2 line3"


def test_safe_filename_length_cap_is_90() -> None:
    assert len(safe_filename("a" * 200)) == 90


def test_safe_filename_empty_when_only_forbidden() -> None:
    assert safe_filename("////") == ""


# --- strip_frontmatter ----------------------------------------------------


def test_strip_frontmatter_removes_leading_block() -> None:
    raw = "---\ntitle: hi\ntype: note\n---\nbody text"
    assert strip_frontmatter(raw) == "body text"


def test_strip_frontmatter_unchanged_without_closing_fence() -> None:
    raw = "---\ntitle: hi\nno closing fence here"
    assert strip_frontmatter(raw) == raw


def test_strip_frontmatter_unchanged_without_leading_fence() -> None:
    raw = "just a regular note\nwith lines"
    assert strip_frontmatter(raw) == raw


# --- _extract_json_object -------------------------------------------------


def test_extract_json_object_fenced() -> None:
    text = 'preamble\n```json\n{"title": "x", "n": 1}\n```\ntrailer'
    assert _extract_json_object(text) == {"title": "x", "n": 1}


def test_extract_json_object_bare_embedded_in_noise() -> None:
    text = 'blah blah {"a": 1, "b": "two"} more noise'
    assert _extract_json_object(text) == {"a": 1, "b": "two"}


def test_extract_json_object_garbage_returns_empty_dict() -> None:
    assert _extract_json_object("no json at all") == {}


def test_extract_json_object_non_object_json_returns_empty_dict() -> None:
    # A bare JSON array is valid JSON but not a dict.
    assert _extract_json_object("[1, 2, 3]") == {}


# --- strip_markdown_fence -------------------------------------------------


def test_strip_markdown_fence_removes_wrapping_fence() -> None:
    assert strip_markdown_fence("```markdown\nhello\n```") == "hello"


def test_strip_markdown_fence_bare_fence() -> None:
    assert strip_markdown_fence("```\nhello world\n```") == "hello world"


def test_strip_markdown_fence_no_fence_unchanged() -> None:
    assert strip_markdown_fence("  plain text  ") == "plain text"


# --- first_nonempty -------------------------------------------------------


def test_first_nonempty_returns_first_truthy_stripped() -> None:
    assert first_nonempty("", "  ", "real") == "real"


def test_first_nonempty_strips_result() -> None:
    assert first_nonempty("  spaced  ") == "spaced"


def test_first_nonempty_all_blank_returns_empty() -> None:
    assert first_nonempty("", "   ", "\n") == ""


# --- as_list --------------------------------------------------------------


def test_as_list_none_returns_empty() -> None:
    assert as_list(None) == []


def test_as_list_passes_through_list_as_strings() -> None:
    assert as_list(["a", 1]) == ["a", "1"]


def test_as_list_wraps_string() -> None:
    assert as_list("solo") == ["solo"]


def test_as_list_wraps_scalar() -> None:
    assert as_list(42) == ["42"]


# --- looks_like_meeting ---------------------------------------------------


def test_looks_like_meeting_korean_and_english_markers() -> None:
    assert looks_like_meeting("오늘 회의 내용")
    assert looks_like_meeting("project Meeting notes")


def test_looks_like_meeting_false_for_unrelated_text() -> None:
    assert not looks_like_meeting("a quiet walk in the park")


# --- recorded_date (POST-FIX cascade) -------------------------------------


def test_recorded_date_uses_start_time_epoch_ms() -> None:
    # start_time is epoch-ms; the resulting date must be the recording's year.
    ts_ms = 1777520000000  # ~2026
    snapshot = {"file_row": {"start_time": ts_ms}}
    expected = datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
    assert recorded_date(snapshot) == expected


def test_recorded_date_out_of_range_start_cascades_to_edit_time() -> None:
    # start_time 0 is falsy/out-of-range; cascade to edit_time (epoch-s),
    # not 1970.
    edit_s = 1777520000  # ~2026
    snapshot = {"file_row": {"start_time": 0, "edit_time": edit_s}}
    result = recorded_date(snapshot)
    expected = datetime.fromtimestamp(edit_s).strftime("%Y-%m-%d")
    assert result == expected
    assert not result.startswith("1970")


def test_recorded_date_cascades_to_today_when_no_valid_ts() -> None:
    snapshot = {"file_row": {"start_time": 0}}
    today = datetime.now().strftime("%Y-%m-%d")
    assert recorded_date(snapshot) == today


def test_recorded_date_empty_snapshot_returns_today() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    assert recorded_date({}) == today


def test_recorded_date_far_future_start_cascades_off_1970() -> None:
    # A nonsense far-future start_time falls outside the 2000-2100 guard and
    # must not be returned; with no other source it falls back to today.
    snapshot = {"file_row": {"start_time": 99999999999999}}
    result = recorded_date(snapshot)
    assert not result.startswith("1970")
