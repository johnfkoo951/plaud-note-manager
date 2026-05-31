from core.integrate import split_integrated


def test_split_integrated_explicit_markers() -> None:
    raw = """
prefix ignored
===FINAL_TRANSCRIPT_BEGIN===
[00:00] A: hello
===FINAL_TRANSCRIPT_END===

===SUMMARY_BEGIN===
## Summary
done
===SUMMARY_END===
suffix ignored
"""

    transcript, summary = split_integrated(raw)

    assert transcript == "[00:00] A: hello"
    assert summary == "## Summary\ndone"


def test_split_integrated_legacy_marker() -> None:
    raw = "## Summary\nlegacy\n\n===TRANSCRIPT===\n# Transcript\nbody"

    transcript, summary = split_integrated(raw)

    assert transcript == "# Transcript\nbody"
    assert summary == "## Summary\nlegacy"


def test_split_integrated_missing_markers_keeps_raw_as_summary() -> None:
    transcript, summary = split_integrated("plain model output")

    assert transcript == ""
    assert summary == "plain model output"
