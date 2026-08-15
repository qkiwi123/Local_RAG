import { Layout, Menu } from "antd";
import { CommentOutlined, DatabaseOutlined } from "@ant-design/icons";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import ChatPage from "./pages/chat/ChatPage";
import KnowledgePage from "./pages/knowledge/KnowledgePage";

const { Sider, Content } = Layout;

export default function App() {
  const location = useLocation();
  const selected =
    location.pathname.startsWith("/knowledge") ? "knowledge" : "chat";

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
