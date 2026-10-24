import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Empty, Input } from "antd";
import { CheckOutlined, CloseOutlined, GlobalOutlined, FolderOutlined, SearchOutlined } from "@ant-design/icons";
import type { TagInfo } from "../../api/types";

interface RetrievalScopePickerProps {
  tags: TagInfo[];
  value: string[];
  onChange: (value: string[]) => void;
  onDismiss?: () => void;
}

export function RetrievalScopePicker({ tags, value, onChange, onDismiss }: RetrievalScopePickerProps) {
  const [search, setSearch] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const matches = useMemo(() => {
    const query = search.trim().toLowerCase();
    return tags.filter((tag) => tag.tag_path.toLowerCase().includes(query) || tag.name.toLowerCase().includes(query));
  }, [tags, search]);

  const toggle = (path: string) => {
    if (value.includes(path)) onChange(value.filter((item) => item !== path));
    else onChange([...value.filter((item) => !item.startsWith(`${path}/`)), path]);
  };
  const navigate = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") { event.preventDefault(); onDismiss?.(); return; }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const options = Array.from(rootRef.current?.querySelectorAll<HTMLButtonElement>("[data-scope-option]:not(:disabled)") ?? []);
    if (!options.length) return;
    event.preventDefault();
    const current = options.indexOf(document.activeElement as HTMLButtonElement);
    const next = current < 0 ? (event.key === "ArrowDown" ? 0 : options.length - 1)
      : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next]?.focus();
  };

  return (
    <div ref={rootRef} className="scope-menu" role="dialog" aria-label="选择检索资料" onKeyDown={navigate}>
      <Input autoFocus prefix={<SearchOutlined />} placeholder="搜索资料标签…" aria-label="搜索检索范围标签"
        allowClear value={search} onChange={(event) => setSearch(event.target.value)} />
      <button type="button" className="scope-menu__all" data-scope-option aria-pressed={!value.length}
        onClick={() => { onChange([]); onDismiss?.(); }}>
        <GlobalOutlined /><span>全部资料</span><small>不限制标签</small>{!value.length && <CheckOutlined />}
      </button>
      {value.length > 0 && <div className="scope-menu__selected" aria-label="已选资料范围">
        {value.map((path) => <button type="button" key={path} aria-label={`移除范围 ${path}`} title={path}
          onClick={() => onChange(value.filter((item) => item !== path))}>
          <span>{tags.find((tag) => tag.tag_path === path)?.name ?? path.split("/").at(-1)}</span><CloseOutlined />
        </button>)}
      </div>}
      <div className="scope-menu__list" aria-label="资料标签列表">
        {matches.map((tag) => {
          const parent = value.find((path) => tag.tag_path.startsWith(`${path}/`));
          const selected = value.includes(tag.tag_path);
          const breadcrumb = tag.tag_path.includes("/") ? tag.tag_path.slice(0, tag.tag_path.lastIndexOf("/")) : "";
          return <button type="button" key={tag.tag_path} data-scope-option
            className={`scope-menu__option${selected || parent ? " scope-menu__option--selected" : ""}`}
            aria-label={`${tag.tag_path}，${tag.document_count} 份文档${parent ? `，已包含在 ${parent}` : ""}`}
            aria-pressed={Boolean(selected || parent)} disabled={Boolean(parent)}
            title={parent ? `已包含在 ${parent} 中；移除父标签后可单独选择` : tag.tag_path}
            onClick={() => toggle(tag.tag_path)}>
            <FolderOutlined />
            <span className="scope-menu__name"><span>{tag.name}</span>{breadcrumb && <small>{breadcrumb}</small>}</span>
            <span className="scope-menu__count">{parent ? "已包含" : `${tag.document_count} 份`}</span>
            <span className="scope-menu__check">{(selected || parent) && <CheckOutlined />}</span>
          </button>;
        })}
        {!matches.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tags.length ? "没有匹配的资料标签" : "还没有资料标签"} />}
      </div>
      <p className="scope-menu__hint">可多选 · 点击即生效 · 父标签包含子标签</p>
    </div>
  );
}
