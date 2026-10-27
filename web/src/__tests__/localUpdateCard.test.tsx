import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { ConfigProvider, message } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocalUpdateCard } from "../pages/settings/LocalUpdateCard";

const mocks = vi.hoisted(() => ({ getLocalUpdateStatus: vi.fn(), inspectLocalUpdate: vi.fn(), applyLocalUpdate: vi.fn() }));
vi.mock('../api/client', () => ({ api: mocks }));
const job = { id: 'a'.repeat(32), phase: 'staged', message: '等待确认', version: 'local-test', notes: '新增会话编辑，保留原数据' };
beforeEach(() => {
  vi.clearAllMocks();
  mocks.getLocalUpdateStatus.mockResolvedValue({ available: true, version: 'old' });
  mocks.inspectLocalUpdate.mockResolvedValue(job);
  mocks.applyLocalUpdate.mockResolvedValue({ ...job, phase: 'queued' });
  vi.stubGlobal('ResizeObserver', class { observe(){} unobserve(){} disconnect(){} });
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){}}));
});
afterEach(() => { cleanup(); message.destroy(); vi.unstubAllGlobals(); });
const show = () => render(<ConfigProvider theme={{token:{motion:false}}}><LocalUpdateCard /></ConfigProvider>);

describe('本地更新入口', () => {
  it('源码模式明确说明，不能选择安装更新', async () => {
    mocks.getLocalUpdateStatus.mockResolvedValue({available:false,message:'本地更新包适用于便携版'});
    show();
    expect(await screen.findByText('本地更新包适用于便携版')).toBeVisible();
    expect(screen.queryByText('选择本地更新包')).not.toBeInTheDocument();
  });
  it('选包只检查，确认前不启动更新，取消仍保留选择', async () => {
    const view = show();
    await screen.findByText('选择本地更新包');
    const file = new File(['synthetic'], 'update.zip', {type:'application/zip'});
    fireEvent.change(view.container.querySelector('input[type=file]')!, {target:{files:[file]}});
    expect(await screen.findByText('可更新至 local-test')).toBeVisible();
    expect(mocks.applyLocalUpdate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'备份并更新'}));
    expect(await screen.findByText('备份并更新项目？')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:/取\s*消/}));
    expect(mocks.applyLocalUpdate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'备份并更新'}));
    fireEvent.click(await screen.findByRole('button',{name:/确认更新/}));
    await waitFor(() => expect(mocks.applyLocalUpdate).toHaveBeenCalledWith(job.id));
    expect(await screen.findByText('等待确认')).toBeVisible();
  });
  it('损坏或不兼容包不出现更新确认按钮', async () => {
    mocks.inspectLocalUpdate.mockRejectedValue(new Error('当前版本不兼容'));
    const view = show();
    await screen.findByText('选择本地更新包');
    fireEvent.change(view.container.querySelector('input[type=file]')!, {target:{files:[new File(['x'],'bad.zip')]}});
    expect(await screen.findByText('当前版本不兼容')).toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'备份并更新'})).not.toBeInTheDocument();
    expect(mocks.applyLocalUpdate).not.toHaveBeenCalled();
  });
  it('重新打开页面仍能显示已完成的更新和刷新入口', async () => {
    mocks.getLocalUpdateStatus.mockResolvedValue({available:true, version:'new',job:{...job,phase:'completed',message:'更新完成，原数据已保留'}});
    show();
    expect(await screen.findByText('更新完成，原数据已保留')).toBeVisible();
    expect(screen.getByRole('button',{name:/刷新页面/})).toBeVisible();
  });
});
