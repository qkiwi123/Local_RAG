import { Button, Input } from "antd";
import { SendOutlined, StopOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";

interface ChatComposerProps {
  value: string;
  streaming: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  scopeControl: ReactNode;
}

export function ChatComposer({
  value,
  streaming,
  onChange,
  onSubmit,
  onStop,
  scopeControl,
}: ChatComposerProps) {
  return (
    <div className="chat-composer" aria-label="提问编辑区">
      <div className="chat-composer__field">
        <Input.TextArea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="请输入您的问题..."
          aria-label="问题输入框"
          autoSize={{ minRows: 2, maxRows: 6 }}
          onPressEnter={(event) => {
            if (!event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
      </div>
      <div className="chat-composer__toolbar">
        {scopeControl}
        <span className="chat-composer__hint">Enter 发送</span>
        <div className="chat-composer__action">
          {streaming ? (
            <Button danger shape="circle" aria-label="停止生成" title="停止生成" icon={<StopOutlined />} onClick={onStop} />
          ) : (
            <Button
              type="primary"
              shape="circle"
              aria-label="发送"
              title="发送 · Shift + Enter 换行"
              icon={<SendOutlined />}
              onClick={onSubmit}
              disabled={!value.trim()}
            />
          )}
        </div>
      </div>
    </div>
  );
}
