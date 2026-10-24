import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

vi.mock("../pages/settings/SettingsPage", () => ({
  default: () => <div data-testid="settings-page">设置内容</div>,
}));

describe("应用级布局", () => {
  beforeEach(() => {
    window.localStorage.removeItem("rag.appearance.theme");
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
    window.localStorage.removeItem("rag.appearance.theme");
    delete document.documentElement.dataset.theme;
    document.documentElement.style.colorScheme = "";
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
    expect(screen.getByRole("link", { name: "设置" })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(screen.getByRole("banner")).toHaveClass("app-header");
    expect(document.querySelector(".app-content-inner")).toBeInTheDocument();
    expect(document.querySelector(".ant-layout-sider")).toBeNull();
    expect(screen.getByTestId("chat-page")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("后端在线")).toBeInTheDocument());
  });

  it("日夜切换立即生效并在重新打开应用后保留选择", async () => {
    const first = render(<MemoryRouter><App /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "切换到夜间模式" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("rag.appearance.theme")).toBe("dark");
    expect(screen.getByTestId("chat-page")).toBeVisible();
    first.unmount();
    render(<MemoryRouter><App /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "切换到日间模式" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem("rag.appearance.theme")).toBe("light");
    await screen.findByText("后端在线");
  });

  it("首次没有偏好时跟随系统夜间模式", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("prefers-color-scheme: dark"), addEventListener() {}, removeEventListener() {} }));
    render(<MemoryRouter><App /></MemoryRouter>);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(screen.getByRole("button", { name: "切换到日间模式" })).toBeVisible();
    await screen.findByText("后端在线");
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

  it("根据设置路由显示设置页和导航选中态", async () => {
    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("settings-page")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /设置/ })).toHaveClass(
      "ant-menu-item-selected",
    );
    expect(screen.getByRole("menuitem", { name: /对话/ })).not.toHaveClass(
      "ant-menu-item-selected",
    );
    await waitFor(() => expect(screen.getByText("后端在线")).toBeInTheDocument());
  });
});
