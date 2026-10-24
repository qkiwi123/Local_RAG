import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RetrievalScopePicker } from "../pages/chat/RetrievalScopePicker";

const tags = [
  { tag_path: "课程", name: "课程", direct_document_count: 0, document_count: 2 },
  { tag_path: "课程/数学", name: "数学", direct_document_count: 1, document_count: 1 },
  { tag_path: "课程/语文", name: "语文", direct_document_count: 1, document_count: 1 },
  { tag_path: "项目", name: "项目", direct_document_count: 1, document_count: 1 },
];

function Harness({ onDismiss = () => {} }: { onDismiss?: () => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  return <><RetrievalScopePicker tags={tags} value={selected} onChange={setSelected} onDismiss={onDismiss} /><output aria-label="已选标签值">{selected.join("|")}</output></>;
}
function choose(path: string) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${path}，`) }));
}

afterEach(cleanup);

describe("输入区资料快捷选择", () => {
  it("搜索并追加范围即时生效，不丢失搜索结果之外的已选项", () => {
    render(<Harness />);
    const search = screen.getByLabelText("搜索检索范围标签");
    fireEvent.change(search, { target: { value: "数学" } });
    choose("课程/数学");
    expect(screen.getByLabelText("已选标签值")).toHaveTextContent(/^课程\/数学$/);
    fireEvent.change(search, { target: { value: "项目" } });
    choose("项目");
    expect(screen.getByLabelText("已选标签值")).toHaveTextContent("课程/数学|项目");
    expect(screen.queryByRole("button", { name: "完成" })).not.toBeInTheDocument();
  });

  it("选择父范围合并冗余子范围，并明确标示子范围已包含", () => {
    render(<Harness />);
    choose("课程/数学");
    choose("课程");
    expect(screen.getByLabelText("已选标签值")).toHaveTextContent(/^课程$/);
    const child = screen.getByRole("button", { name: "课程/语文，1 份文档，已包含在 课程" });
    expect(child).toBeDisabled();
    expect(child).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "移除范围 课程" }));
    expect(screen.getByRole("button", { name: "课程/语文，1 份文档" })).toBeEnabled();
  });

  it("搜索时可直接移除已选项，不用清空搜索再找回原标签", () => {
    render(<Harness />);
    choose("课程/数学");
    choose("项目");
    fireEvent.change(screen.getByLabelText("搜索检索范围标签"), { target: { value: "数学" } });
    fireEvent.click(screen.getByRole("button", { name: "移除范围 项目" }));
    expect(screen.getByLabelText("已选标签值")).toHaveTextContent(/^课程\/数学$/);
    expect(screen.getByLabelText("搜索检索范围标签")).toHaveValue("数学");
  });

  it("全部资料一键重置并收起，方向键和 Escape 可操作", () => {
    const dismiss = vi.fn();
    render(<Harness onDismiss={dismiss} />);
    choose("项目");
    const search = screen.getByLabelText("搜索检索范围标签");
    search.focus();
    fireEvent.keyDown(search, { key: "ArrowDown" });
    const all = screen.getByRole("button", { name: /全部资料/ });
    expect(all).toHaveFocus();
    fireEvent.keyDown(all, { key: "ArrowDown" });
    expect(screen.getByRole("button", { name: "课程，2 份文档" })).toHaveFocus();
    search.focus();
    fireEvent.keyDown(search, { key: "ArrowUp" });
    expect(screen.getByRole("button", { name: "项目，1 份文档" })).toHaveFocus();
    fireEvent.keyDown(search, { key: "Escape" });
    expect(dismiss).toHaveBeenCalledTimes(1);
    fireEvent.click(all);
    expect(screen.getByLabelText("已选标签值")).toBeEmptyDOMElement();
    expect(dismiss).toHaveBeenCalledTimes(2);
  });

  it("搜索无匹配时已选范围仍可查看和删除", () => {
    render(<Harness />);
    choose("项目");
    fireEvent.change(screen.getByLabelText("搜索检索范围标签"), { target: { value: "不存在" } });
    expect(screen.getByText("没有匹配的资料标签")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "移除范围 项目" }));
    expect(screen.getByLabelText("已选标签值")).toBeEmptyDOMElement();
  });
});
