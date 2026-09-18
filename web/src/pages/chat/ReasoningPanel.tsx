import { useEffect, useRef, useState } from "react";

interface ReasoningPanelProps {
  text: string;
  answering: boolean;
  active: boolean;
  failed: boolean;
}

export function ReasoningPanel({ text, answering, active, failed }: ReasoningPanelProps) {
  const [expanded, setExpanded] = useState(active && !answering);
  const answerStarted = useRef(answering);
  const wasActive = useRef(active);
  const contentRef = useRef<HTMLDivElement>(null);
  const followTail = useRef(true);

  useEffect(() => {
    if (active && !wasActive.current) {
      setExpanded(!answering);
      answerStarted.current = answering;
      followTail.current = true;
    }
    wasActive.current = active;
    if (answering && !answerStarted.current) {
      setExpanded(false);
      answerStarted.current = true;
    }
  }, [active, answering]);

  useEffect(() => {
    const content = contentRef.current;
    if (content && active && followTail.current) content.scrollTop = content.scrollHeight;
  }, [text, active, expanded]);

  if (!text.trim()) return null;
  const label = failed ? "思考内容（生成未完成）" : active && !answering ? "思考中" : "思考内容";
  return (
    <section className="chat-reasoning" aria-label="模型思考">
      <button
        type="button"
        className="chat-reasoning-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
        {label}
        <span>{expanded ? "收起" : "展开"}</span>
      </button>
      {expanded && (
        <div ref={contentRef} className="chat-reasoning-content" onScroll={(event) => {
          const node = event.currentTarget;
          followTail.current = node.scrollHeight - node.scrollTop - node.clientHeight < 32;
        }}>{text}</div>
      )}
    </section>
  );
}
