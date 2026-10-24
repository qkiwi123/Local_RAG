"""Recover incomplete portable ingests before exposing retrieval after restart.

Keep original/uploading files for diagnosis. Remove partial retrieval artifacts
before marking failed; a cleanup error aborts startup instead of serving them.
"""
import logging
import sqlite3

logger = logging.getLogger(__name__)


def recover_interrupted_ingests() -> dict:
    from backend.config import settings
    from backend.services.document_store import mark_failed
    from backend.services.unit_store import delete_document_units
    from backend.services.vector_store import delete_document_by_id

    connection = sqlite3.connect(settings.METADATA_DB_PATH, timeout=10)
    try:
        identifiers = [row[0] for row in connection.execute(
            "SELECT document_id FROM documents WHERE status = 'processing'")]
    finally:
        connection.close()
    for document_id in identifiers:
        try:
            delete_document_by_id(document_id)
            delete_document_units(document_id, db_path=settings.METADATA_DB_PATH)
            mark_failed(document_id, "上次运行中断，文档未完成入库；请删除失败记录后重新上传")
        except Exception:
            logger.exception("便携启动：清理中断文档失败，拒绝开放检索：%s", document_id)
            raise
    if identifiers:
        logger.warning("便携启动：已将%d份中断文档标为失败并移除部分检索内容；上传文件保留", len(identifiers))
    return {"recovered": len(identifiers)}
