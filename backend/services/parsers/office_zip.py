"""DOCX/PPTX ZIP 容器的解析前资源边界校验。"""

from __future__ import annotations

from pathlib import Path
from zipfile import BadZipFile, ZipFile


MAX_ZIP_ENTRIES = 10_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_SINGLE_ENTRY_BYTES = 50 * 1024 * 1024
MAX_SINGLE_XML_BYTES = 20 * 1024 * 1024


def validate_office_package(file_path: str) -> None:
    path = Path(file_path)
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise ValueError(
                    f"Office 文件包含过多 ZIP entry: {len(entries)}"
                )

            total_size = 0
            for entry in entries:
                if entry.flag_bits & 0x1:
                    raise ValueError("Office 文件不能包含加密 ZIP entry")
                if "\x00" in entry.filename:
                    raise ValueError("Office ZIP entry 名称非法")
                if entry.file_size > MAX_SINGLE_ENTRY_BYTES:
                    raise ValueError(
                        f"Office ZIP entry 解压后过大: {entry.filename}"
                    )
                if (
                    entry.filename.lower().endswith((".xml", ".rels"))
                    and entry.file_size > MAX_SINGLE_XML_BYTES
                ):
                    raise ValueError(
                        f"Office XML entry 解压后过大: {entry.filename}"
                    )
                total_size += entry.file_size
                if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise ValueError("Office 文件解压后总大小超出限制")
    except BadZipFile as error:
        raise ValueError("Office 文件不是有效的 ZIP 容器") from error
