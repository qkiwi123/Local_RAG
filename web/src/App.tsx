import { useEffect, useState } from "react";
import { Alert, Layout, Menu } from "antd";
import { CommentOutlined, DatabaseOutlined } from "@ant-design/icons";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import ChatPage from "./pages/chat/ChatPage";
import KnowledgePage from "./pages/knowledge/KnowledgePage";

const { Sider, Content } = Layout;

export default function App() {
  const location = useLocation();
  const selected =
    location.pathname.startsWith("/knowledge") ? "knowledge" : "chat";
  const [backendDown, setBackendDown] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health")
      .then((response) => {
        if (!cancelled) setBackendDown(!response.ok);
      })
      .catch(() => {
        if (!cancelled) setBackendDown(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider theme="light" width={220}>
        <div
          style={{
            padding: 16,
            fontSize: 18,
            fontWeight: 700,
          }}
        >
          📚 RAG 知识库
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={[
            {
              key: "chat",
              icon: <CommentOutlined />,
              label: <Link to="/">💬 对话</Link>,
            },
            {
              key: "knowledge",
              icon: <DatabaseOutlined />,
              label: <Link to="/knowledge">📚 知识库管理</Link>,
            },
          ]}
        />
      </Sider>
      <Layout>
        {backendDown && (
          <Alert
            type="error"
            showIcon
            banner
            message="无法连接后端服务，请确认后端已启动（scripts/start.ps1）"
          />
        )}
        <Content style={{ padding: 16, overflow: "auto", height: "100vh" }}>
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}
