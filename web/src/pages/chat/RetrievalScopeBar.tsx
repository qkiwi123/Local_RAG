import { Button, Tag } from "antd";
import { CloseOutlined, DatabaseOutlined } from "@ant-design/icons";
import type { TagInfo } from "../../api/types";
import { TagTreeSelect } from "../../components/TagTreeSelect";

interface RetrievalScopeBarProps {
  tags: TagInfo[];
  value: string[];
  onChange: (value: string[]) => void;
}

export function RetrievalScopeBar({
  tags,
  value,
  onChange,
}: RetrievalScopeBarProps) {
  const allDocuments = value.length === 0;

  return (
    <section className="retrieval-scope" aria-labelledby="retrieval-scope-title">
      <div className="retrieval-scope__heading">
        <div className="retrieval-scope__label-row">
          <DatabaseOutlined aria-hidden="true" />
          <h2 id="retrieval-scope-title" className="retrieval-scope__title">
            检索范围
          </h2>
          <Tag color={allDocuments ? "blue" : "geekblue"} className="retrieval-scope__mode">
            {allDocuments ? "全部知识库" : `已选 ${value.length} 个标签`}
          </Tag>
        </div>
        <p className="retrieval-scope__hint">
          {allDocuments
            ? "下一次提问将检索全部已入库文档"
            : "父级包含子标签，多标签取并集；范围用于下一次提问"}
        </p>
      </div>

      <div className="retrieval-scope__controls">
        <div className="retrieval-scope__select">
          <TagTreeSelect
            tags={tags}
            value={value}
            onChange={onChange}
            ariaLabel="会话检索范围标签"
            placeholder="选择标签以缩小检索范围"
          />
        </div>
        {!allDocuments && (
          <Button
            className="retrieval-scope__clear"
            icon={<CloseOutlined />}
            onClick={() => onChange([])}
          >
            清除
          </Button>
        )}
      </div>

      {!allDocuments && (
        <div className="retrieval-scope__tags" aria-label="当前检索标签">
          {value.map((tag) => (
            <Tag
              key={tag}
              closable
              onClose={() => onChange(value.filter((item) => item !== tag))}
            >
              {tag}
            </Tag>
          ))}
        </div>
      )}
    </section>
  );
}
