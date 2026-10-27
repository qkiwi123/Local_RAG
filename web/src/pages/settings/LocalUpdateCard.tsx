import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Modal, Space, Spin, Typography, Upload, message } from "antd";
import { UploadOutlined, ReloadOutlined } from "@ant-design/icons";
import { api } from "../../api/client";
import type { LocalUpdateJob, LocalUpdateStatus } from "../../api/types";

const terminal = ["completed", "rolled_back", "rejected", "recovery_required"];

export function LocalUpdateCard() {
  const [status, setStatus] = useState<LocalUpdateStatus | null>(null);
  const [selected, setSelected] = useState<LocalUpdateJob | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [offline, setOffline] = useState(false);
  const attempts = useRef(0);
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const result = await api.getLocalUpdateStatus();
        if (alive) {
          setStatus(result);
          if (result.job && !terminal.includes(result.job.phase)) setTracking(true);
        }
      } catch { if (alive) setStatus({ available: false, message: "暂时无法获取更新状态，也可使用便携目录内的更新.cmd。" }); }
    })();
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    if (!tracking) return;
    let alive = true;
    const timer = window.setInterval(() => {
      void api.getLocalUpdateStatus().then((result) => {
        if (!alive) return;
        setStatus(result); setOffline(false);
        if (result.job && terminal.includes(result.job.phase)) setTracking(false);
      }).catch(() => {
        if (!alive) return;
        setOffline(true);
        if (++attempts.current >= 300) setTracking(false);
      });
    }, 2000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [tracking]);
  const inspect = async (file: File) => {
    setBusy(true); setSelected(null);
    try { setSelected(await api.inspectLocalUpdate(file)); }
    catch (error) { message.error((error as Error).message); }
    finally { setBusy(false); }
    return Upload.LIST_IGNORE;
  };
  const apply = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const job = await api.applyLocalUpdate(selected.id);
      setStatus((previous) => ({ ...previous!, job }));
      setSelected(null); setConfirm(false); attempts.current = 0; setTracking(true);
    } catch (error) { message.error((error as Error).message); }
    finally { setBusy(false); }
  };
  return <Card title="关于与本地更新" className="settings-card">
    <Typography.Paragraph type="secondary">选择应用更新 ZIP，保留文档、会话、设置和索引。更新时项目会暂时停止。</Typography.Paragraph>
    {status?.version && <Typography.Paragraph>当前版本：<Typography.Text code>{status.version}</Typography.Text></Typography.Paragraph>}
    {status?.available ? <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Upload accept=".zip" showUploadList={false} beforeUpload={inspect} disabled={busy || tracking}>
        <Button icon={<UploadOutlined />} loading={busy} disabled={tracking}>选择本地更新包</Button>
      </Upload>
      {selected && <Alert type="info" showIcon title={`可更新至 ${selected.version}`} description={<>
        <p style={{ whiteSpace: "pre-wrap" }}>{selected.notes}</p>
        <Button type="primary" onClick={() => setConfirm(true)}>备份并更新</Button>
      </>} />}
      {tracking && <Alert type="info" showIcon icon={<Spin size="small" />} title={offline ? "项目正在停止或重启，正在等待连接恢复…" : status.job?.message ?? "正在更新…"}
        description="无需重复点击。若页面长时间无法恢复，可运行便携目录内的更新.cmd查看或恢复。" />}
      {!tracking && status.job && terminal.includes(status.job.phase) && <Alert showIcon
        type={status.job.phase === "completed" ? "success" : "warning"} title={status.job.message}
        description={status.job.error} action={status.job.phase === "completed"
          ? <Button icon={<ReloadOutlined />} onClick={() => window.location.reload()}>刷新页面</Button> : undefined} />}
      {!tracking && offline && <Alert type="warning" showIcon title="尚未连接到更新后的项目"
        description="请运行更新.cmd检查恢复记录；原备份保存在便携目录的 state/updates 中。" />}
    </Space> : <Typography.Text type="secondary">{status?.message ?? "正在读取本地更新状态…"}</Typography.Text>}
    <Modal open={confirm} title="备份并更新项目？" okText="确认更新" cancelText="取消"
      confirmLoading={busy} onCancel={() => !busy && setConfirm(false)} onOk={() => void apply()}>
      <p>将等待当前任务结束，正常停止项目，备份原程序与整套数据，然后安装所选更新。</p>
      <p>模型与运行环境继续保留。更新失败会恢复原版本与对应备份；请保留更新期间的目录和文件。</p>
    </Modal>
  </Card>;
}
