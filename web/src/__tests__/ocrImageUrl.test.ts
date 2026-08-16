import { describe, expect, it } from "vitest";
import { api } from "../api/client";

describe("ocrImageUrl", () => {
  it("使用包含 /id/ 段的路由并转义参数", () => {
    expect(api.ocrImageUrl("doc-1", "abcdef0123456789")).toBe(
      "/api/documents/id/doc-1/ocr-images/abcdef0123456789",
    );
  });

  it("对 document_id 和 image_ref 做 URI 编码", () => {
    expect(api.ocrImageUrl("a/b", "c d")).toBe(
      "/api/documents/id/a%2Fb/ocr-images/c%20d",
    );
  });
});
