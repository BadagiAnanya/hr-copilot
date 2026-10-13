from app.ingestion.chunker import chunk_markdown


def test_single_heading():
    md = "# Overview\n\nSome body text here."
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert chunks[0].section == "Overview"
    assert "Some body text" in chunks[0].content


def test_nested_breadcrumb():
    md = """# Leave
## Sick Leave
### Entitlement
You get 12 days per year.

### Documentation
Submit a certificate.
"""
    chunks = chunk_markdown(md)
    sections = [c.section for c in chunks]
    assert "Leave > Sick Leave > Entitlement" in sections
    assert "Leave > Sick Leave > Documentation" in sections


def test_long_section_splits():
    long = "This is a sentence. " * 200   # ~4000 chars
    md = f"# Section\n\n{long}"
    chunks = chunk_markdown(md)
    assert len(chunks) > 1
    assert all(len(c.content) <= 1300 for c in chunks)


def test_empty_sections_skipped():
    md = "# A\n\n# B\n\ncontent"
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert chunks[0].section == "B"
