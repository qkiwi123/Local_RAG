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
}

export function TagTreeSelect({
  tags,
  value,
  onChange,
  placeholder,
  allowClear = true,
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
      style={{ width: "100%" }}
    />
  );
}
