import { useMemo } from "react";
import { TreeSelect } from "antd";
import type { TagInfo } from "../api/types";

interface TagNode {
  value: string;
  title: string;
  children: TagNode[];
}

function buildTree(tags: TagInfo[]): TagNode[] {
  const byPath = new Map<string, TagNode>();
  const roots: TagNode[] = [];
  const sorted = [...tags].sort((a, b) => a.tag_path.localeCompare(b.tag_path));
  for (const tag of sorted) {
    const count = tag.document_count;
    const node: TagNode = {
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

interface TagTreeSelectProps {
  tags: TagInfo[];
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  allowClear?: boolean;
}

export function TagTreeSelect({
  tags,
  value,
  onChange,
  placeholder,
  allowClear = true,
}: TagTreeSelectProps) {
  const treeData = useMemo(() => buildTree(tags), [tags]);
  return (
    <TreeSelect
      treeData={treeData}
      value={value}
      onChange={onChange}
      multiple
      treeCheckable
      showCheckedStrategy={TreeSelect.SHOW_PARENT}
      placeholder={placeholder ?? "按标签筛选（可多选，含子标签；不选 = 全部）"}
      allowClear={allowClear}
      style={{ width: "100%" }}
    />
  );
}
