import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatComposer } from "../pages/chat/ChatComposer";

beforeEach(() => vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("提问编辑区的键盘输入", () => {
  it("中文输入法确认候选字和 Shift Enter 不误发送，普通 Enter 发送", () => {
    const submit = vi.fn();
    render(<ChatComposer value="问题" streaming={false} onChange={vi.fn()} onSubmit={submit} onStop={vi.fn()} scopeControl={<button>全部资料</button>} />);
    const input = screen.getByLabelText("问题输入框");
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", keyCode: 229, isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", keyCode: 13, shiftKey: true });
    expect(submit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", keyCode: 13 });
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it("生成中保持停止操作可用，输入草稿不替换按钮", () => {
    const stop = vi.fn();
    render(<ChatComposer value="接下来的问题" streaming onChange={vi.fn()} onSubmit={vi.fn()} onStop={stop} scopeControl={<button>全部资料</button>} />);
    expect(screen.queryByRole("button", { name: "发送" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));
    expect(stop).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("问题输入框")).toHaveValue("接下来的问题");
  });
});
