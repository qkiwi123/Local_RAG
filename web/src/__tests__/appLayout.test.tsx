import { cleanup, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import App from "../App";

vi.mock("../pages/chat/ChatPage", () => ({
  default: () => <div data-testid="chat-page">对话内容</div>,
}));

vi.mock("../pages/knowledge/KnowledgePage", () => ({
  default: () => <div data-testid="knowledge-page">知识库内容</div>,
}));

describe("应用级布局", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("提供品牌、横向导航和内容壳", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "RAG 知识库首页" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "对话" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "知识库管理" })).toHaveAttribute(
      "href",
      "/knowledge",
    );
    expect(screen.getByRole("banner")).toHaveClass("app-header");
    expect(document.querySelector(".app-content-inner")).toBeInTheDocument();
    expect(document.querySelector(".ant-layout-sider")).toBeNull();
    expect(screen.getByTestId("chat-page")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("后端在线")).toBeInTheDocument());
  });

  it("根据知识库路由显示对应选中态", async () => {
    render(
      <MemoryRouter initialEntries={["/knowledge"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("knowledge-page")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /知识库管理/ })).toHaveClass(
      "ant-menu-item-selected",
    );
    expect(screen.getByRole("menuitem", { name: /对话/ })).not.toHaveClass(
      "ant-menu-item-selected",
    );
    await waitFor(() => expect(screen.getByText("后端在线")).toBeInTheDocument());
  });
});
