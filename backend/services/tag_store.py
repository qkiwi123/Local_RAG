"""多级树形标签存储。

标签是任意深度的 '/' 分隔路径（如 '课程课件/计算机基础/数据结构'），层级由
路径前缀推导；文档与标签多对多。检索过滤时先展开「标签及其全部子孙标签」，
再换算为 document_id 集合（并集语义）。
"""

from __future__ import annotations

from typing import Optional

from backend.config import settings
from backend.services.document_store import _connection, _now
from backend.services.metadata_schema import require_structured_database

MAX_TAG_DEPTH = 6
MAX_TAG_LENGTH = 200


class TagValidationError(ValueError):
    """标签路径不符合格式约束。"""


class TagNotFoundError(KeyError):
    """标签（或其前缀子树）不存在。"""


def _db_path(db_path: Optional[str]) -> str:
    return str(db_path or settings.METADATA_DB_PATH)


def _ensure_schema(db_path: Optional[str]) -> None:
    require_structured_database(_db_path(db_path))


def normalize_tag_path(raw: str) -> str:
    """规范化并校验标签路径；非法输入抛 TagValidationError。"""
    if not isinstance(raw, str):
        raise TagValidationError("标签必须是字符串")
    stripped = raw.strip()
    if not stripped:
        raise TagValidationError("标签不能为空")
    segments = [segment.strip() for segment in stripped.split("/")]
    if any(not segment for segment in segments):
        raise TagValidationError("标签路径不能包含空段或首尾斜杠")
    if len(segments) > MAX_TAG_DEPTH:
        raise TagValidationError(f"标签层级不能超过 {MAX_TAG_DEPTH} 级")
    path = "/".join(segments)
    if len(path) > MAX_TAG_LENGTH:
        raise TagValidationError(f"标签总长度不能超过 {MAX_TAG_LENGTH} 字符")
    return path


def _parent_paths(path: str) -> list[str]:
    """返回包含自身在内的全部前缀路径，父级在前。"""
    segments = path.split("/")
    return ["/".join(segments[: i + 1]) for i in range(len(segments))]


def _insert_tag_row(connection, path: str, created_at: str) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO tags (tag_path, name, created_at)
        VALUES (?, ?, ?)
        """,
        (path, path.rsplit("/", 1)[-1], created_at),
    )


def _get_tag_row(path: str, db_path: Optional[str] = None) -> Optional[dict]:
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT tag_path, name, created_at FROM tags WHERE tag_path = ?",
            (path,),
        ).fetchone()
    return dict(row) if row else None


def create_tag(tag_path: str, db_path: Optional[str] = None) -> dict:
    """创建标签并自动补齐缺失的父级节点；已存在时幂等返回。"""
    path = normalize_tag_path(tag_path)
    _ensure_schema(db_path)
    created_at = _now()
    with _connection(db_path) as connection:
        for parent in _parent_paths(path):
            _insert_tag_row(connection, parent, created_at)
    return _get_tag_row(path, db_path)


def list_tag_tree(db_path: Optional[str] = None) -> list[dict]:
    """返回全部标签及子树文档数（仅统计 ready 文档）。

    每个元素为 {tag_path, name, document_count}；document_count 是该标签及其
    全部子孙标签直接命中的 ready 文档总数（即选中该标签可检索到的文档数）。
    树形结构由前端按 tag_path 前缀组装。
    """
    _ensure_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            "SELECT tag_path, name FROM tags ORDER BY tag_path"
        ).fetchall()
        counts = dict(
            connection.execute(
                """
                SELECT dt.tag_path, COUNT(*)
                FROM document_tags AS dt
                JOIN documents AS d ON d.document_id = dt.document_id
                WHERE d.status = 'ready'
                GROUP BY dt.tag_path
                """
            ).fetchall()
        )

    tags = [dict(row) for row in rows]
    for tag in tags:
        path = tag["tag_path"]
        prefix = path + "/"
        tag["document_count"] = sum(
            count
            for tag_path, count in counts.items()
            if tag_path == path or tag_path.startswith(prefix)
        )
    return tags


def _subtree_paths(connection, path: str) -> list[str]:
    """返回以 path 为根的全部现有标签路径（含自身）；Python 前缀过滤避免 LIKE 通配。"""
    rows = connection.execute(
        "SELECT tag_path FROM tags ORDER BY tag_path"
    ).fetchall()
    prefix = path + "/"
    return [
        row["tag_path"]
        for row in rows
        if row["tag_path"] == path or row["tag_path"].startswith(prefix)
    ]


def rename_tag(
    old_path: str,
    new_path: str,
    db_path: Optional[str] = None,
) -> None:
    """重命名标签及其整个子树；文档关联随路径前缀一并更新。"""
    old = normalize_tag_path(old_path)
    new = normalize_tag_path(new_path)
    if old == new:
        return
    if new.startswith(old + "/") or old.startswith(new + "/"):
        raise TagValidationError("不能把标签重命名为自身的父级或子级路径")
    _ensure_schema(db_path)
    with _connection(db_path) as connection:
        subtree = _subtree_paths(connection, old)
        if not subtree:
            raise TagNotFoundError(f"标签不存在: {old}")
        other_paths = {
            row["tag_path"]
            for row in connection.execute("SELECT tag_path FROM tags").fetchall()
        } - set(subtree)
        if any(
            other == new or other.startswith(new + "/")
            for other in other_paths
        ):
            raise TagValidationError(f"目标路径已存在其他标签: {new}")

        linked = connection.execute(
            """
            SELECT document_id, tag_path FROM document_tags
            WHERE tag_path IN ({})
            """.format(", ".join("?" for _ in subtree)),
            subtree,
        ).fetchall()
        connection.execute(
            "DELETE FROM tags WHERE tag_path IN ({})".format(
                ", ".join("?" for _ in subtree)
            ),
            subtree,
        )
        # 重建整个子树节点（含无文档关联的中间节点），再恢复文档关联
        for old_tag_path in subtree:
            _insert_tag_row(
                connection, new + old_tag_path[len(old):], _now()
            )
        for parent in _parent_paths(new):
            _insert_tag_row(connection, parent, _now())
        for row in linked:
            renamed_path = new + row["tag_path"][len(old):]
            connection.execute(
                "INSERT INTO document_tags (document_id, tag_path) VALUES (?, ?)",
                (row["document_id"], renamed_path),
            )


def delete_tag(tag_path: str, db_path: Optional[str] = None) -> int:
    """删除标签及其整个子树；文档关联由外键级联清理。返回删除的标签节点数。"""
    path = normalize_tag_path(tag_path)
    _ensure_schema(db_path)
    with _connection(db_path) as connection:
        subtree = _subtree_paths(connection, path)
        if not subtree:
            raise TagNotFoundError(f"标签不存在: {path}")
        cursor = connection.execute(
            "DELETE FROM tags WHERE tag_path IN ({})".format(
                ", ".join("?" for _ in subtree)
            ),
            subtree,
        )
    return cursor.rowcount


def set_document_tags(
    document_id: str,
    paths: list[str],
    db_path: Optional[str] = None,
) -> list[str]:
    """整体替换文档的标签集；缺失的标签自动创建（含父级）。返回排序后的标签。"""
    normalized = sorted({normalize_tag_path(path) for path in paths})
    _ensure_schema(db_path)
    created_at = _now()
    with _connection(db_path) as connection:
        for path in normalized:
            for parent in _parent_paths(path):
                _insert_tag_row(connection, parent, created_at)
        connection.execute(
            "DELETE FROM document_tags WHERE document_id = ?", (document_id,)
        )
        connection.executemany(
            "INSERT INTO document_tags (document_id, tag_path) VALUES (?, ?)",
            [(document_id, path) for path in normalized],
        )
    return normalized


def get_document_tags(
    document_id: str,
    db_path: Optional[str] = None,
) -> list[str]:
    _ensure_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT tag_path FROM document_tags
            WHERE document_id = ? ORDER BY tag_path
            """,
            (document_id,),
        ).fetchall()
    return [row["tag_path"] for row in rows]


def delete_document_tags(
    document_id: str,
    db_path: Optional[str] = None,
) -> None:
    """清理文档的全部标签关联（上传失败补偿使用）。"""
    _ensure_schema(db_path)
    with _connection(db_path) as connection:
        connection.execute(
            "DELETE FROM document_tags WHERE document_id = ?", (document_id,)
        )


def expand_tag_paths(
    paths: list[str],
    db_path: Optional[str] = None,
) -> list[str]:
    """展开标签集合：每个标签带上它实际存在的全部子孙标签，去重排序。"""
    normalized = {normalize_tag_path(path) for path in paths}
    if not normalized:
        return []
    _ensure_schema(db_path)
    with _connection(db_path) as connection:
        rows = connection.execute(
            "SELECT tag_path FROM tags ORDER BY tag_path"
        ).fetchall()
    all_paths = [row["tag_path"] for row in rows]
    expanded = set()
    for path in normalized:
        prefix = path + "/"
        expanded.add(path)
        expanded.update(
            other for other in all_paths if other.startswith(prefix)
        )
    return sorted(expanded)


def resolve_document_ids(
    paths: list[str],
    db_path: Optional[str] = None,
) -> list[str]:
    """把标签（含子孙）换算为 ready 文档 ID 集合，多标签取并集。"""
    expanded = expand_tag_paths(paths, db_path)
    if not expanded:
        return []
    _ensure_schema(db_path)
    placeholders = ", ".join("?" for _ in expanded)
    with _connection(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT dt.document_id
            FROM document_tags AS dt
            JOIN documents AS d ON d.document_id = dt.document_id
            WHERE dt.tag_path IN ({placeholders})
              AND d.status = 'ready'
            ORDER BY dt.document_id
            """,
            expanded,
        ).fetchall()
    return [row["document_id"] for row in rows]
