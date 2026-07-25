from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from backend.services.parsers import office_zip


def _make_zip(path, entries):
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)


def test_office_package_rejects_invalid_zip(tmp_path):
    source = tmp_path / "invalid.docx"
    source.write_bytes(b"not-a-zip")

    with pytest.raises(ValueError, match="有效的 ZIP"):
        office_zip.validate_office_package(str(source))


def test_office_package_rejects_excess_entry_count(tmp_path, monkeypatch):
    source = tmp_path / "many.docx"
    _make_zip(source, [("a.xml", "a"), ("b.xml", "b")])
    monkeypatch.setattr(office_zip, "MAX_ZIP_ENTRIES", 1)

    with pytest.raises(ValueError, match="过多 ZIP entry"):
        office_zip.validate_office_package(str(source))


def test_office_package_rejects_total_and_single_xml_size(tmp_path, monkeypatch):
    source = tmp_path / "large.pptx"
    _make_zip(source, [("ppt/slides/slide1.xml", "123456")])

    monkeypatch.setattr(office_zip, "MAX_SINGLE_XML_BYTES", 5)
    with pytest.raises(ValueError, match="XML entry"):
        office_zip.validate_office_package(str(source))

    monkeypatch.setattr(office_zip, "MAX_SINGLE_XML_BYTES", 100)
    monkeypatch.setattr(office_zip, "MAX_TOTAL_UNCOMPRESSED_BYTES", 5)
    with pytest.raises(ValueError, match="总大小"):
        office_zip.validate_office_package(str(source))
