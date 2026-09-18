import { afterEach, describe, expect, it, vi } from "vitest";
import { parseEventLine, streamChat } from "../api/stream";

describe("parseEventLine", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

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

  it("响应未结束时立即分发已经完整到达的 delta 行", async () => {
    const encoder = new TextEncoder();
    let finish: (() => void) | undefined;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            `${JSON.stringify({ event: "delta", text: "第一段" })}\n`,
          ),
        );
        finish = () => {
          controller.enqueue(
            encoder.encode(
              `${JSON.stringify({ event: "delta", text: "第二段" })}\n`,
            ),
          );
          controller.close();
        };
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, body, status: 200 } as Response),
    );
    const onDelta = vi.fn();
    let settled = false;

    const pending = streamChat({}, { onDelta }).then((result) => {
      settled = true;
      return result;
    });

    await vi.waitFor(() => expect(onDelta).toHaveBeenCalledTimes(1));
    expect(onDelta).toHaveBeenLastCalledWith({
      event: "delta",
      text: "第一段",
    });
    expect(settled).toBe(false);

    finish?.();
    const result = await pending;
    expect(onDelta).toHaveBeenCalledTimes(2);
    expect(result.events).toHaveLength(2);
    expect(result.aborted).toBe(false);
  });

  it("请求头返回前中止也按主动停止处理", async () => {
    const error = { name: "AbortError", message: "aborted" };
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(error));

    await expect(streamChat({}, {})).resolves.toEqual({
      events: [],
      aborted: true,
    });
  });

  it("思考与正文分别分发，支持 UTF-8 跨片与末尾无换行", async () => {
    const bytes = new TextEncoder().encode(
      JSON.stringify({ event: "reasoning_delta", text: "先思考" }) + "\n" +
      JSON.stringify({ event: "delta", text: "再回答" }) + "\n" +
      JSON.stringify({ event: "done" }),
    );
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
      controller.close();
    } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body, status: 200 }));
    const onReasoningDelta = vi.fn();
    const onDelta = vi.fn();
    const onDone = vi.fn();
    await streamChat({}, { onReasoningDelta, onDelta, onDone });
    expect(onReasoningDelta).toHaveBeenCalledExactlyOnceWith({ event: "reasoning_delta", text: "先思考" });
    expect(onDelta).toHaveBeenCalledExactlyOnceWith({ event: "delta", text: "再回答" });
    expect(onDone).toHaveBeenCalledOnce();
  });
});
