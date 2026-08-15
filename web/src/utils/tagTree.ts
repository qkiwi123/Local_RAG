import type { TagInfo } from "../api/types";

export interface TagTreeNode {
  value: string;
  title: string;
  children: TagTreeNode[];
}

/**
 * 把扁平标签列表组装为树：父子关系由 tag_path 前缀推导；
 * 节点标题展示「本层 N · 含子级 M」双层计数。
 */
export function buildTagTree(tags: TagInfo[]): TagTreeNode[] {
  const byPath = new Map<string, TagTreeNode>();
  const roots: TagTreeNode[] = [];
  const sorted = [...tags].sort((a, b) => a.tag_path.localeCompare(b.tag_path));
  for (const tag of sorted) {
    const count = tag.document_count;
    const node: TagTreeNode = {
      value: tag.tag_path,
      title: `${tag.name}（${
        tag.direct_document_count === count
          ? `${count} 文档`
          : `本层 ${tag.direct_document_count} · 含子级 ${count}`
      }）`,
      children: [],
    };
    byPath.set(tag.tag_path, node);
    const slash = tag.tag_path.lastIndexOf("/");
    if (slash < 0) {
      roots.push(node);
    } else {
      const parent = byPath.get(tag.tag_path.slice(0, slash));
      if (parent) {
        parent.children.push(node);
      } else {
        roots.push(node);
      }
    }
  }
  return roots;
}
