import { useMemo } from "react";
import { TreeSelect } from "antd";
import type { TagInfo } from "../api/types";
import { buildTagTree } from "../utils/tagTree";

interface TagTreeSelectProps {
  tags: TagInfo[];
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  allowClear?: boolean;
  ariaLabel?: string;
}

export function TagTreeSelect({
  tags,
  value,
  onChange,
  placeholder,
  allowClear = true,
  ariaLabel = "标签选择器",
}: TagTreeSelectProps) {
  const treeData = useMemo(() => buildTagTree(tags), [tags]);
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
      aria-label={ariaLabel}
      className="tag-tree-picker"
      maxTagCount="responsive"
      maxTagPlaceholder={(omitted) => `+${omitted.length}`}
      showSearch
      treeNodeFilterProp="title"
      treeDefaultExpandAll
      notFoundContent="暂无匹配标签"
    />
  );
}
