import { useRef, useState } from "react";
import { Button, Popover } from "antd";
import { CloseOutlined, DownOutlined, ReadOutlined } from "@ant-design/icons";
import type { TagInfo } from "../../api/types";
import { RetrievalScopePicker } from "./RetrievalScopePicker";

interface RetrievalScopeBarProps {
  tags: TagInfo[];
  value: string[];
  onChange: (value: string[]) => void;
  editMode?: boolean;
}

// A composer tool: selecting a source takes effect immediately for the next send.
export function RetrievalScopeBar({ tags, value, onChange, editMode = false }: RetrievalScopeBarProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const label = value.length === 0 ? "全部资料" : value.length === 1
    ? (tags.find((tag) => tag.tag_path === value[0])?.name ?? value[0].split("/").at(-1))
    : `${value.length} 个资料范围`;
  const dismiss = () => { setOpen(false); triggerRef.current?.focus(); };

  return (
    <div className={`scope-tool${value.length ? " scope-tool--filtered" : ""}`}>
      <Popover trigger="click" placement="topLeft" arrow={false} open={open} onOpenChange={setOpen}
        destroyOnHidden content={<RetrievalScopePicker tags={tags} value={value} onChange={onChange} onDismiss={dismiss} />}>
        <Button ref={triggerRef} type="text" size="small" className="scope-tool__trigger" icon={<ReadOutlined />}
          aria-label={`${editMode ? "修改本条检索范围" : "选择检索资料"}，当前${label}`} aria-expanded={open} aria-haspopup="dialog"
          title={value.length ? `仅检索：${value.join("、")}（含子标签）` : "从全部已入库资料中查找"}>
          <span className="scope-tool__label">{editMode ? `修改范围 · ${label}` : label}</span><DownOutlined className="scope-tool__chevron" />
        </Button>
      </Popover>
      {value.length > 0 && <Button size="small" type="text" className="scope-tool__reset" icon={<CloseOutlined />}
        aria-label="清除检索范围" title="恢复全部资料" onClick={() => onChange([])} />}
    </div>
  );
}
