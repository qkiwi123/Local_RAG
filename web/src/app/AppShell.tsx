import { useEffect, useState, type ReactNode } from "react";
import { Alert, Layout, Menu } from "antd";
import { CommentOutlined, DatabaseOutlined } from "@ant-design/icons";
import { Link, useLocation } from "react-router-dom";

const { Header, Content } = Layout;

interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const location = useLocation();
  const selected =
    location.pathname.startsWith("/knowledge") ? "knowledge" : "chat";
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
                  label: <Link to="/knowledge">知识库管理</Link>,
                },
              ]}
            />
          </nav>

          <div className="app-header-meta">
            <span className="app-workspace-label">知识工作台</span>
            <span
              className={`app-health app-health-${
                !healthChecked ? "checking" : backendDown ? "down" : "up"
              }`}
              aria-live="polite"
            >
              <span className="app-health-dot" aria-hidden="true" />
              {!healthChecked
                ? "检查后端"
                : backendDown
                  ? "后端离线"
                  : "后端在线"}
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

      <Content className="app-content">
        <div className="app-content-inner">{children}</div>
      </Content>
    </Layout>
  );
}
