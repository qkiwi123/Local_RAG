"""多级树形标签存储测试；每个测试使用独立临时数据库。"""

import pytest

from backend.services.document_store import (
    create_document,
    delete_record,
    mark_ready,
)
from backend.services.metadata_schema import bootstrap_structured_database
from backend.services.tag_store import (
    TagNotFoundError,
    TagValidationError,
    create_tag,
    delete_document_tags,
    delete_tag,
    expand_tag_paths,
    get_document_tags,
    list_tag_tree,
    normalize_tag_path,
    rename_tag,
    resolve_document_ids,
    set_document_tags,
)

CREATED_AT = "2026-08-13T00:00:00+00:00"


@pytest.fixture
def db_path(temp_dir):
    path = f"{temp_dir}/metadata.db"
    bootstrap_structured_database(path, created_at=CREATED_AT)
    return path


def _ready_document(db_path, document_id, name="文档.pdf"):
    create_document(
        document_id,
        name,
        f"{document_id}.pdf",
        10,
        db_path=db_path,
    )
    mark_ready(document_id, 1, db_path=db_path)


class TestNormalize:
    def test_valid_path_is_joined_and_stripped(self):
        assert normalize_tag_path(" 课程课件 / 数据结构 ") == "课程课件/数据结构"

    def test_single_level_is_allowed(self):
        assert normalize_tag_path("规章制度") == "规章制度"

    def test_rejects_empty_or_blank(self):
        for bad in ("", "   ", None, 123):
            with pytest.raises(TagValidationError):
                normalize_tag_path(bad)

    def test_rejects_leading_trailing_or_double_slashes(self):
        for bad in ("/课程", "课程/", "课程//结构", "/课程/结构/"):
            with pytest.raises(TagValidationError, match="空段或首尾斜杠"):
                normalize_tag_path(bad)

    def test_rejects_too_deep_or_too_long(self):
        with pytest.raises(TagValidationError, match="层级"):
            normalize_tag_path("/".join(f"L{i}" for i in range(7)))
        with pytest.raises(TagValidationError, match="总长度"):
            normalize_tag_path("课" * 250)


class TestCreateAndTree:
    def test_create_tag_auto_creates_parents_and_is_idempotent(self, db_path):
        first = create_tag("课程课件/数据结构", db_path)
        again = create_tag("课程课件/数据结构", db_path)

        assert first["tag_path"] == "课程课件/数据结构"
        assert first["name"] == "数据结构"
        assert again["tag_path"] == first["tag_path"]
        paths = {tag["tag_path"] for tag in list_tag_tree(db_path)}
        assert {"课程课件", "课程课件/数据结构"} <= paths

    def test_tree_counts_ready_documents_with_subtree_semantics(self, db_path):
        _ready_document(db_path, "doc-1")
        _ready_document(db_path, "doc-2")
        set_document_tags("doc-1", ["课程课件/数据结构"], db_path)
        set_document_tags("doc-2", ["课程课件/操作系统"], db_path)

        tree = {tag["tag_path"]: tag for tag in list_tag_tree(db_path)}

        assert tree["课程课件/数据结构"]["document_count"] == 1
        assert tree["课程课件/数据结构"]["direct_document_count"] == 1
        assert tree["课程课件/操作系统"]["document_count"] == 1
        # 父标签本层没有直接文档，但子标签命中 2 篇
        assert tree["课程课件"]["document_count"] == 2
        assert tree["课程课件"]["direct_document_count"] == 0

    def test_creating_tag_never_attaches_documents(self, db_path):
        _ready_document(db_path, "doc-1")
        set_document_tags("doc-1", ["其他标签"], db_path)

        create_tag("课程课件", db_path)
        create_tag("课程课件/数据结构", db_path)

        tree = {tag["tag_path"]: tag for tag in list_tag_tree(db_path)}
        assert tree["课程课件"]["direct_document_count"] == 0
        assert tree["课程课件"]["document_count"] == 0
        assert tree["课程课件/数据结构"]["document_count"] == 0
        assert get_document_tags("doc-1", db_path) == ["其他标签"]

    def test_tree_ignores_failed_documents(self, db_path):
        create_document("doc-1", "坏.pdf", "doc-1.pdf", 1, db_path=db_path)
        set_document_tags("doc-1", ["课程课件"], db_path)

        tree = {tag["tag_path"]: tag for tag in list_tag_tree(db_path)}
        assert tree["课程课件"]["document_count"] == 0


class TestRename:
    def test_rename_updates_subtree_and_document_links(self, db_path):
        _ready_document(db_path, "doc-1")
        set_document_tags("doc-1", ["旧分类/子类"], db_path)

        rename_tag("旧分类", "新分类", db_path)

        assert get_document_tags("doc-1", db_path) == ["新分类/子类"]
        paths = {tag["tag_path"] for tag in list_tag_tree(db_path)}
        assert "旧分类" not in paths and "新分类/子类" in paths

    def test_rename_missing_tag_raises(self, db_path):
        with pytest.raises(TagNotFoundError):
            rename_tag("不存在", "新", db_path)

    def test_rename_into_existing_subtree_conflicts(self, db_path):
        create_tag("A/x", db_path)
        create_tag("B", db_path)
        with pytest.raises(TagValidationError, match="已存在"):
            rename_tag("A", "B", db_path)

    def test_rename_to_own_descendant_rejected(self, db_path):
        create_tag("A/B", db_path)
        with pytest.raises(TagValidationError, match="父级或子级"):
            rename_tag("A", "A/B", db_path)


class TestDelete:
    def test_delete_removes_subtree_and_clears_links_but_keeps_documents(
        self, db_path
    ):
        _ready_document(db_path, "doc-1")
        set_document_tags("doc-1", ["课程课件/数据结构", "保留"], db_path)

        removed = delete_tag("课程课件", db_path)

        assert removed == 2
        assert get_document_tags("doc-1", db_path) == ["保留"]
        assert {tag["tag_path"] for tag in list_tag_tree(db_path)} == {"保留"}

    def test_delete_missing_tag_raises(self, db_path):
        with pytest.raises(TagNotFoundError):
            delete_tag("不存在", db_path)


class TestDocumentTags:
    def test_set_replaces_and_sorts(self, db_path):
        _ready_document(db_path, "doc-1")
        set_document_tags("doc-1", ["B", "A"], db_path)
        assert get_document_tags("doc-1", db_path) == ["A", "B"]

        set_document_tags("doc-1", ["C"], db_path)
        assert get_document_tags("doc-1", db_path) == ["C"]

    def test_set_creates_missing_tags_and_parents(self, db_path):
        _ready_document(db_path, "doc-1")
        set_document_tags("doc-1", ["新分类/新子类"], db_path)
        paths = {tag["tag_path"] for tag in list_tag_tree(db_path)}
        assert {"新分类", "新分类/新子类"} <= paths

    def test_set_invalid_path_raises(self, db_path):
        _ready_document(db_path, "doc-1")
        with pytest.raises(TagValidationError):
            set_document_tags("doc-1", ["坏/标签/"], db_path)

    def test_delete_document_tags_clears_links(self, db_path):
        _ready_document(db_path, "doc-1")
        set_document_tags("doc-1", ["A"], db_path)
        delete_document_tags("doc-1", db_path)
        assert get_document_tags("doc-1", db_path) == []

    def test_document_deletion_cascades_links(self, db_path):
        _ready_document(db_path, "doc-1")
        set_document_tags("doc-1", ["A"], db_path)
        delete_record("doc-1", db_path)
        assert resolve_document_ids(["A"], db_path) == []


class TestExpandAndResolve:
    def test_expand_includes_existing_descendants_only(self, db_path):
        create_tag("A/B/C", db_path)
        create_tag("A/other", db_path)

        assert expand_tag_paths(["A"], db_path) == ["A", "A/B", "A/B/C", "A/other"]
        assert expand_tag_paths(["A/B"], db_path) == ["A/B", "A/B/C"]
        assert expand_tag_paths([], db_path) == []

    def test_resolve_returns_union_of_ready_documents(self, db_path):
        _ready_document(db_path, "doc-1")
        _ready_document(db_path, "doc-2")
        _ready_document(db_path, "doc-3")
        set_document_tags("doc-1", ["课程课件/数据结构"], db_path)
        set_document_tags("doc-2", ["规章制度"], db_path)
        set_document_tags("doc-3", ["课程课件/操作系统"], db_path)

        assert resolve_document_ids(["课程课件"], db_path) == ["doc-1", "doc-3"]
        assert resolve_document_ids(
            ["课程课件/数据结构"], db_path
        ) == ["doc-1"]
        assert resolve_document_ids(
            ["课程课件", "规章制度"], db_path
        ) == ["doc-1", "doc-2", "doc-3"]
        assert resolve_document_ids([], db_path) == []
        assert resolve_document_ids(["不存在的标签"], db_path) == []
