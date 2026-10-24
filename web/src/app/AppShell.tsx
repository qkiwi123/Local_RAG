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

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health")
      .then((response) => {
        if (!cancelled) {
          setBackendDown(!response.ok);
          setHealthChecked(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBackendDown(true);
          setHealthChecked(true);
        }
      });
    return () => {
      cancelled = true;
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
            <span className="app-workspace-label">知识工作台</span>
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
