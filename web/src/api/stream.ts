import type { StreamEvent } from "./types";

/**
 * 解析 NDJSON 事件行；容忍分片（跨 chunk 的不完整行由调用方缓冲）。
 */
export function parseEventLine(line: string): StreamEvent | null {
  const trimmed = line.trim();
  if (!trimmed) {
    return null;
  }
  try {
    return JSON.parse(trimmed) as StreamEvent;
  } catch {
    return null;
  }
}

export interface ChatStreamHandlers {
  onMeta?: (event: Extract<StreamEvent, { event: "meta" }>) => void;
  onDelta?: (event: Extract<StreamEvent, { event: "delta" }>) => void;
  onReasoningDelta?: (event: Extract<StreamEvent, { event: "reasoning_delta" }>) => void;
  onCitations?: (event: Extract<StreamEvent, { event: "citations" }>) => void;
  onDone?: (event: Extract<StreamEvent, { event: "done" }>) => void;
  onError?: (event: Extract<StreamEvent, { event: "error" }>) => void;
}

export interface ChatStreamResult {
  events: StreamEvent[];
  aborted: boolean;
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

export async function streamChat(
  payload: Record<string, unknown>,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<ChatStreamResult> {
  let response: Response;
  try {
    response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (isAbortError(error)) {
      return { events: [], aborted: true };
    }
    throw error;
  }

  if (!response.ok || !response.body) {
    let message = `HTTP ${response.status}`;
    let code: string | undefined;
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail === "object") {
        message = detail.message || message;
        code = detail.code;
      }
    } catch {
      // 忽略
    }
    throw { status: response.status, code, message };
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const events: StreamEvent[] = [];

  const flush = () => {
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const event = parseEventLine(line);
      if (!event) {
        continue;
      }
      events.push(event);
      switch (event.event) {
        case "meta":
          handlers.onMeta?.(event);
          break;
        case "delta":
          handlers.onDelta?.(event);
          break;
        case "reasoning_delta":
          handlers.onReasoningDelta?.(event);
          break;
        case "citations":
          handlers.onCitations?.(event);
          break;
        case "done":
          handlers.onDone?.(event);
          break;
        case "error":
          handlers.onError?.(event);
          break;
      }
    }
  };

  let aborted = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      flush();
    }
    buffer += decoder.decode();
    // 兼容最后一条事件未附换行的上游响应。
    if (buffer.trim()) buffer += "\n";
    flush();
  } catch (error) {
    if (isAbortError(error)) {
      aborted = true;
    } else {
      throw error;
    }
  }
  return { events, aborted };
}
