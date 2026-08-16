import React from "react";
import { Tag, Typography } from "antd";
import { api } from "../api/client";
import type { LocatedBlock } from "../api/types";

const { Paragraph, Title, Text } = Typography;

export const OCR_BADGE_TEXT = "AI 识别，可能存在误差";

const preWrapStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  marginBottom: 4,
};

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
  documentId?: string;
  showOcrImages?: boolean;
  seenOcrImages?: Set<string>;
}

function levelTitle(level: number): 1 | 2 | 3 | 4 | 5 {
  return Math.min(Math.max(level, 1), 5) as 1 | 2 | 3 | 4 | 5;
}

export function BlockView({
  block,
  highlightStart,
  highlightEnd,
  documentId,
  showOcrImages,
  seenOcrImages,
}: BlockViewProps) {
  const ocr = block.attributes?.ocr === true;
  const leadingHeadingLevel =
    typeof block.attributes?.ocr_leading_heading_level === "number"
      ? block.attributes.ocr_leading_heading_level
      : null;
  const imageRef =
    typeof block.attributes?.ocr_image_ref === "string"
      ? block.attributes.ocr_image_ref
      : null;

  const showImage =
    Boolean(showOcrImages && documentId && imageRef) &&
    Boolean(seenOcrImages && !seenOcrImages.has(imageRef as string));
  if (showImage && seenOcrImages) {
    seenOcrImages.add(imageRef as string);
  }

  const renderHighlightedLine = (text: string, lineStart: number) => {
    if (highlightStart !== undefined && highlightEnd !== undefined) {
      return highlightText(text, lineStart, highlightStart, highlightEnd);
    }
    return text;
  };

  const renderOcrParagraph = () => {
    const lines = block.text.split("\n");
    if (leadingHeadingLevel && lines.length > 1) {
      const heading = lines[0];
      const rest = lines.slice(1).join("\n");
      const restStart = block.start_char + heading.length + 1;
      return (
        <div>
          <Title level={levelTitle(leadingHeadingLevel)} style={{ marginBottom: 4 }}>
            {renderHighlightedLine(heading, block.start_char)}
          </Title>
          {rest ? (
            <div style={preWrapStyle}>{renderHighlightedLine(rest, restStart)}</div>
          ) : null}
        </div>
      );
    }
    return (
      <div style={preWrapStyle}>
        {highlightStart !== undefined && highlightEnd !== undefined
          ? highlightText(block.text, block.start_char, highlightStart, highlightEnd)
          : block.text}
      </div>
    );
  };

  const renderBody = () => {
    switch (block.block_type) {
      case "heading": {
        const level = levelTitle(block.heading_level ?? 1);
        return <Title level={level}>{body()}</Title>;
      }
      case "quote":
        return <blockquote style={quoteStyle}>{body()}</blockquote>;
      case "code":
        return (
          <pre style={codeStyle}>
            <code>{body()}</code>
          </pre>
        );
      case "list_item": {
        const lines = block.text.split("\n");
        if (ocr && lines.length > 1) {
          let offset = block.start_char;
          return (
            <div>
              {lines.map((line, index) => {
                const lineStart = offset;
                offset += line.length + 1;
                return (
                  <div key={index} style={{ marginLeft: (block.list_level ?? 0) * 1.5 + "em" }}>
                    • {renderHighlightedLine(line, lineStart)}
                  </div>
                );
              })}
            </div>
          );
        }
        return (
          <div style={{ marginLeft: (block.list_level ?? 0) * 1.5 + "em" }}>
            • {body()}
          </div>
        );
      }
      case "table":
        return renderTable();
      case "paragraph":
      default:
        if (ocr) {
          return renderOcrParagraph();
        }
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

  const content = renderBody();
  const badge = ocr ? (
    <Tag color="orange" style={{ marginLeft: 8 }}>
      {OCR_BADGE_TEXT}
    </Tag>
  ) : null;

  return (
    <div>
      {showImage ? (
        <div className="ocr-compare">
          <a
            className="ocr-compare__image"
            href={api.ocrImageUrl(documentId as string, imageRef as string)}
            target="_blank"
            rel="noreferrer"
            title="点击查看原图"
          >
            <img
              src={api.ocrImageUrl(documentId as string, imageRef as string)}
              alt="OCR 识别原图"
            />
          </a>
          <div className="ocr-compare__text">{content}</div>
        </div>
      ) : (
        content
      )}
      {badge}
    </div>
  );
}

interface StructuredRendererProps {
  blocks: LocatedBlock[];
  highlightStart?: number;
  highlightEnd?: number;
  documentId?: string;
  showOcrImages?: boolean;
}

export function StructuredRenderer({
  blocks,
  highlightStart,
  highlightEnd,
  documentId,
  showOcrImages,
}: StructuredRendererProps) {
  const seenOcrImages = new Set<string>();
  return (
    <div>
      {blocks.map((block) => (
        <BlockView
          key={block.block_id}
          block={block}
          highlightStart={highlightStart}
          highlightEnd={highlightEnd}
          documentId={documentId}
          showOcrImages={showOcrImages}
          seenOcrImages={seenOcrImages}
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
