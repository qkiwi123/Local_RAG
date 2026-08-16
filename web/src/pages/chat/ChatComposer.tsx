import { Button, Input } from "antd";
import { SendOutlined, StopOutlined } from "@ant-design/icons";

interface ChatComposerProps {
  value: string;
  streaming: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
}

export function ChatComposer({
  value,
  streaming,
  onChange,
  onSubmit,
  onStop,
}: ChatComposerProps) {
  return (
    <div className="chat-composer">
      <div className="chat-composer__field">
        <Input.TextArea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="请输入您的问题..."
          aria-label="问题输入框"
          autoSize={{ minRows: 1, maxRows: 6 }}
          onPressEnter={(event) => {
            if (!event.shiftKey) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
        <span className="chat-composer__hint">Enter 发送 · Shift + Enter 换行</span>
      </div>
      <div className="chat-composer__action">
        {streaming ? (
          <Button danger icon={<StopOutlined />} onClick={onStop}>
            停止
          </Button>
        ) : (
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={onSubmit}
            disabled={!value.trim()}
          >
            发送
          </Button>
        )}
      </div>
    </div>
  );
}
