import { useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Layout, Menu, Tooltip } from "antd";
import {
  CommentOutlined,
  DatabaseOutlined,
  SettingOutlined,
  MoonOutlined,
  SunOutlined,
} from "@ant-design/icons";
import { Link, useLocation } from "react-router-dom";
import { useAppTheme } from "./ThemeContext";

const { Header, Content } = Layout;

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const location = useLocation();
  const { mode, toggle } = useAppTheme();
  const selected = location.pathname.startsWith("/knowledge")
    ? "knowledge"
    : location.pathname.startsWith("/settings")
      ? "settings"
      : "chat";
  const [backendDown, setBackendDown] = useState(false);
  const [healthChecked, setHealthChecked] = useState(false);
  const [backendPort, setBackendPort] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const checkHealth = () => fetch("/api/health")
      .then(async (response) => {
        const payload = response.ok ? await response.json() : null;
        if (!cancelled) {
          setBackendDown(!response.ok);
          const port = payload?.runtime?.port;
          setBackendPort(Number.isInteger(port) && port > 0 && port <= 65535 ? port : null);
          setHealthChecked(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBackendDown(true);
          setBackendPort(null);
          setHealthChecked(true);
        }
      });
    void checkHealth();
    const timer = window.setInterval(checkHealth, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <Layout className="app-shell">
      <Header className="app-header">
        <div className="app-header-inner">
          <Link className="app-brand" to="/" aria-label="RAG 知识库首页">
            <span className="app-brand-mark" aria-hidden="true">
              R
            </span>
            <span className="app-brand-name">RAG 知识库</span>
          </Link>

          <nav className="app-navigation" aria-label="主导航">
            <Menu
              className="app-menu"
              mode="horizontal"
              selectedKeys={[selected]}
              items={[
                {
                  key: "chat",
                  icon: <CommentOutlined />,
                  label: <Link to="/">对话</Link>,
                },
                {
                  key: "knowledge",
                  icon: <DatabaseOutlined />,
                  label: <Link to="/knowledge" aria-label="知识库管理"><span className="app-nav-full-label">知识库管理</span><span className="app-nav-short-label">知识库</span></Link>,
                },
                {
                  key: "settings",
                  icon: <SettingOutlined />,
                  label: <Link to="/settings">设置</Link>,
                },
              ]}
            />
          </nav>

          <div className="app-header-meta">
            <Tooltip title={mode === "dark" ? "切换到日间模式" : "切换到夜间模式"}>
              <Button className="app-theme-toggle" type="text"
                aria-label={mode === "dark" ? "切换到日间模式" : "切换到夜间模式"}
                icon={mode === "dark" ? <SunOutlined /> : <MoonOutlined />} onClick={toggle} />
            </Tooltip>
            <Tooltip title="后端正在监听的服务端口；开发时显示后端端口。">
              <span className="app-runtime-port" aria-label={`服务端口 ${backendPort ?? "未知"}`}>
                <span className="app-runtime-port__label">端口</span> {backendPort ?? "—"}
              </span>
            </Tooltip>
            <span
              className={`app-health app-health-${
                !healthChecked ? "checking" : backendDown ? "down" : "up"
              }`}
              aria-live="polite"
              aria-label={!healthChecked ? "检查后端" : backendDown ? "后端离线" : "后端在线"}
            >
              <span className="app-health-dot" aria-hidden="true" />
              <span className="app-health-label">{!healthChecked
                ? "检查后端"
                : backendDown
                  ? "后端离线"
                  : "后端在线"}</span>
            </span>
          </div>
        </div>
      </Header>

      {backendDown && (
        <Alert
          className="app-backend-alert"
          type="error"
          showIcon
          banner
          title="无法连接后端服务，请确认后端已启动（scripts/start.ps1）"
        />
      )}

      <Content className={`app-content${selected === "settings" ? " app-content--settings" : ""}`}>
        <div className="app-content-inner">{children}</div>
      </Content>
    </Layout>
  );
}
