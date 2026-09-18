import { cleanup, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import type { LocatedBlock } from "../api/types";
import { StructuredRenderer } from "../components/StructuredRenderer";

function block(
  id: string,
  text: string,
  imageRef?: string,
): LocatedBlock {
  return {
    block_id: id,
    block_type: "paragraph",
    text,
    sequence: Number(id.replace(/\D/g, "")) || 0,
    attributes: imageRef
      ? { ocr: true, ocr_image_ref: imageRef }
      : {},
    start_char: 0,
    end_char: text.length,
  };
}

afterEach(cleanup);

describe("StructuredRenderer OCR 原图分组", () => {
  it("把相邻且引用同一原图的全部 OCR 块放入同一个右栏", () => {
    render(
      <StructuredRenderer
        blocks={[
          block("block-1", "第一段", "page-image-1"),
          block("block-2", "第二段", "page-image-1"),
          block("block-3", "第三段", "page-image-1"),
          block("block-4", "原生后续文本"),
        ]}
        documentId="doc-1"
        showOcrImages
      />,
    );

    expect(screen.getAllByAltText("OCR 识别原图")).toHaveLength(1);
    const comparison = document.querySelector(".ocr-compare");
    expect(comparison).not.toBeNull();
    const textColumn = comparison!.querySelector(".ocr-compare__text");
    expect(textColumn).not.toBeNull();
    expect(within(textColumn as HTMLElement).getByText("第一段")).toBeVisible();
    expect(within(textColumn as HTMLElement).getByText("第二段")).toBeVisible();
    expect(within(textColumn as HTMLElement).getByText("第三段")).toBeVisible();
    expect(within(textColumn as HTMLElement).queryByText("原生后续文本")).toBeNull();
  });

  it("普通文本和另一张图片会结束当前图片分组且不改变顺序", () => {
    const blocks = [
      block("block-1", "图一第一段", "image-1"),
      block("block-2", "图一第二段", "image-1"),
      block("block-3", "中间普通文本"),
      block("block-4", "图二文字", "image-2"),
    ];

    render(
      <StructuredRenderer blocks={blocks} documentId="doc-1" showOcrImages />,
    );
    expect(screen.getAllByAltText("OCR 识别原图")).toHaveLength(2);
    const comparisons = document.querySelectorAll(".ocr-compare");
    expect(comparisons).toHaveLength(2);
    expect(within(comparisons[0] as HTMLElement).getByText("图一第一段")).toBeVisible();
    expect(within(comparisons[0] as HTMLElement).getByText("图一第二段")).toBeVisible();
    expect(within(comparisons[0] as HTMLElement).queryByText("中间普通文本")).toBeNull();
    expect(within(comparisons[1] as HTMLElement).getByText("图二文字")).toBeVisible();

    const renderedText = document.body.textContent ?? "";
    expect(renderedText.indexOf("图一第二段")).toBeLessThan(
      renderedText.indexOf("中间普通文本"),
    );
    expect(renderedText.indexOf("中间普通文本")).toBeLessThan(
      renderedText.indexOf("图二文字"),
    );
  });

  it("关闭原图展示时保持原来的纯文本渲染", () => {
    render(
      <StructuredRenderer
        blocks={[
          block("block-1", "第一段", "image-1"),
          block("block-2", "第二段", "image-1"),
        ]}
        documentId="doc-1"
      />,
    );
    expect(screen.queryByAltText("OCR 识别原图")).toBeNull();
    expect(document.querySelector(".ocr-compare")).toBeNull();
    expect(screen.getByText("第一段")).toBeVisible();
    expect(screen.getByText("第二段")).toBeVisible();
  });
});
