import React from "react";
import { Tag, Typography } from "antd";
import type { LocatedBlock } from "../api/types";

const { Paragraph, Title, Text } = Typography;

export const OCR_BADGE_TEXT = "AI 识别，可能存在误差";

/** 把块内与 [highlightStart, highlightEnd) 相交的字符用 <mark> 包裹。 */
function highlightText(
  text: string,
  blockStart: number,
  highlightStart: number,
  highlightEnd: number,
): React.ReactNode {
  if (highlightEnd <= highlightStart) {
    return text;
  }
  const start = Math.max(0, highlightStart - blockStart);
  const end = Math.min(text.length, highlightEnd - blockStart);
  if (end <= start) {
    return text;
  }
  return (
    <>
      {text.slice(0, start)}
      <mark>{text.slice(start, end)}</mark>
      {text.slice(end)}
    </>
  );
}

interface BlockViewProps {
  block: LocatedBlock;
  highlightStart?: number;
  highlightEnd?: number;
}

export function BlockView({ block, highlightStart, highlightEnd }: BlockViewProps) {
  const ocr = block.attributes?.ocr === true;
  const badge = ocr ? (
    <Tag color="orange" style={{ marginLeft: 8 }}>
      {OCR_BADGE_TEXT}
    </Tag>
  ) : null;

  const renderBody = () => {
    switch (block.block_type) {
      case "heading": {
        const level = Math.min(Math.max(block.heading_level ?? 1, 1), 5);
        const Level = [Title, Title, Title, Title, Title][level - 1];
        return <Level level={level as 1 | 2 | 3 | 4 | 5}>{body()}</Level>;
      }
      case "quote":
        return <blockquote style={quoteStyle}>{body()}</blockquote>;
      case "code":
        return (
          <pre style={codeStyle}>
            <code>{body()}</code>
          </pre>
        );
      case "list_item":
        return (
          <div style={{ marginLeft: (block.list_level ?? 0) * 1.5 + "em" }}>
            • {body()}
          </div>
        );
      case "table":
        return renderTable();
      case "paragraph":
      default:
        return <Paragraph style={{ marginBottom: 4 }}>{body()}</Paragraph>;
    }
  };

  const body = () => {
    if (highlightStart !== undefined && highlightEnd !== undefined) {
      return highlightText(block.text, block.start_char, highlightStart, highlightEnd);
    }
    return block.text;
  };

  const renderTable = () => {
    const rows = block.table_rows ?? [];
    if (!rows.length) {
      return <Text type="secondary">（空表格）</Text>;
    }
    // 表格相交时整块标记高亮（单元格级高亮由文本级渲染的引用预览复用块级近似）
    const intersects =
      highlightStart !== undefined &&
      highlightEnd !== undefined &&
      block.end_char > highlightStart &&
      block.start_char < highlightEnd;
    return (
      <table style={tableStyle} className={intersects ? "preview-table-hit" : ""}>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  style={rowIndex === 0 ? headCellStyle : cellStyle}
                >
                  {cell.replace(/\|/g, "\\|")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  };

  return (
    <div>
      {renderBody()}
      {badge}
    </div>
  );
}

interface StructuredRendererProps {
  blocks: LocatedBlock[];
  highlightStart?: number;
  highlightEnd?: number;
}

export function StructuredRenderer({
  blocks,
  highlightStart,
  highlightEnd,
}: StructuredRendererProps) {
  return (
    <div>
      {blocks.map((block) => (
        <BlockView
          key={block.block_id}
          block={block}
          highlightStart={highlightStart}
          highlightEnd={highlightEnd}
        />
      ))}
    </div>
  );
}

const quoteStyle: React.CSSProperties = {
  borderLeft: "3px solid #d9d9d9",
  paddingLeft: 12,
  color: "#595959",
  margin: "4px 0",
};

const codeStyle: React.CSSProperties = {
  background: "#f6f6f6",
  padding: 8,
  borderRadius: 4,
  overflowX: "auto",
};

const tableStyle: React.CSSProperties = {
  borderCollapse: "collapse",
  margin: "8px 0",
};

const cellStyle: React.CSSProperties = {
  border: "1px solid #d9d9d9",
  padding: "4px 8px",
};

const headCellStyle: React.CSSProperties = {
  ...cellStyle,
  background: "#fafafa",
  fontWeight: 600,
};
