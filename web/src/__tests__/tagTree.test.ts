import { describe, expect, it } from "vitest";
import { buildTagTree } from "../utils/tagTree";

describe("buildTagTree", () => {
  it("组装父子层级并按路径排序", () => {
    const tags = [
      { tag_path: "课程课件/数据结构", name: "数据结构", direct_document_count: 1, document_count: 1 },
      { tag_path: "课程课件", name: "课程课件", direct_document_count: 0, document_count: 2 },
      { tag_path: "课程课件/操作系统", name: "操作系统", direct_document_count: 1, document_count: 1 },
      { tag_path: "规章制度", name: "规章制度", direct_document_count: 1, document_count: 1 },
    ];

    const tree = buildTagTree(tags);

    expect(tree.map((node) => node.value)).toEqual(["规章制度", "课程课件"]);
    const course = tree.find((node) => node.value === "课程课件");
    expect(course?.children.map((node) => node.value)).toEqual([
      "课程课件/操作系统",
      "课程课件/数据结构",
    ]);
  });

  it("父级标题展示双层计数，叶子只展示单层计数", () => {
    const tree = buildTagTree([
      { tag_path: "课程课件", name: "课程课件", direct_document_count: 0, document_count: 3 },
      { tag_path: "课程课件/数据结构", name: "数据结构", direct_document_count: 3, document_count: 3 },
    ]);

    expect(tree[0].title).toContain("本层 0 · 含子级 3");
    expect(tree[0].children[0].title).toContain("3 文档");
  });

  it("父级缺失时子级作为根节点挂载", () => {
    const tree = buildTagTree([
      { tag_path: "孤儿/子", name: "子", direct_document_count: 1, document_count: 1 },
    ]);

    expect(tree.map((node) => node.value)).toEqual(["孤儿/子"]);
  });
});
