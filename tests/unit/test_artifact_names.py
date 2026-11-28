from xaa.storage.artifact_names import safe_upload_filename


def test_upload_filename_keeps_a_safe_basename_and_mime_extension():
    assert safe_upload_filename(r"C:\Users\owner\Quarterly report.PDF", "application/pdf") == (
        "Quarterly report.pdf"
    )


def test_upload_filename_drops_traversal_controls_and_dangerous_extensions():
    assert safe_upload_filename("../../CON.exe\n", "audio/wav") == "_CON.wav"


def test_upload_filename_uses_a_bounded_fallback():
    assert safe_upload_filename(None, "text/plain") == "xaa-attachment.txt"
    result = safe_upload_filename(f"{'a' * 240}.md", "text/markdown")
    assert len(result) <= 180
    assert result.endswith(".md")


def test_ogg_voice_notes_use_safe_ogg_extension():
    assert safe_upload_filename("voice-note.bin", "audio/ogg") == "voice-note.ogg"
