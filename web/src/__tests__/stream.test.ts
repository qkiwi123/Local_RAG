import { describe, expect, it } from "vitest";
import { parseEventLine } from "../api/stream";

describe("parseEventLine", () => {
  it("解析合法事件行", () => {
    const event = parseEventLine(
      JSON.stringify({ event: "delta", text: "你好" }),
    );
    expect(event).toEqual({ event: "delta", text: "你好" });
  });

  it("忽略空行与非法 JSON", () => {
    expect(parseEventLine("")).toBeNull();
    expect(parseEventLine("   ")).toBeNull();
    expect(parseEventLine("{not json")).toBeNull();
  });

  it("解析 meta / citations / done / error 事件", () => {
    expect(
      parseEventLine(
        JSON.stringify({
          event: "meta",
          request_id: "r1",
          standalone_question: "问题",
        }),
      )?.event,
    ).toBe("meta");
    expect(
      parseEventLine(JSON.stringify({ event: "citations", citations: [] }))?.event,
    ).toBe("citations");
    expect(
      parseEventLine(JSON.stringify({ event: "done", assistant_message_id: "m1" }))
        ?.event,
    ).toBe("done");
    expect(
      parseEventLine(
        JSON.stringify({ event: "error", code: "llm_error", message: "失败" }),
      )?.event,
    ).toBe("error");
  });
});
