import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Form,
  Modal,
  Result,
  Space,
  Spin,
  Typography,
  message,
} from "antd";
import { ApiOutlined, DatabaseOutlined, EyeOutlined, FilterOutlined, ReloadOutlined, ScissorOutlined, ExperimentOutlined } from "@ant-design/icons";
import { api, ApiError } from "../../api/client";
import type {
  LlmSettingsPatch,
  ChunkingSettingsPatch,
  RetrievalSettingsPatch,
  SettingsPatchResponse,
  SettingsReadResponse,
  SettingsRuntimeStatus,
  VisionSettingsPatch,
  VisionTestTask,
} from "../../api/types";
import type { ApiKeyIntent, SettingsFormValues } from "./formTypes";
import { ModelSettingsCard } from "./ModelSettingsCard";
import { VisionSettingsCard } from "./VisionSettingsCard";
import { RetrievalSettingsCard } from "./RetrievalSettingsCard";
import { ChunkingSettingsCard } from "./ChunkingSettingsCard";
import { DiagnosticsSettingsCard } from "./DiagnosticsSettingsCard";
import { RuntimeStatusCard } from "./RuntimeStatusCard";
import { SettingsApplyBar } from "./SettingsApplyBar";
import "./settings-page.css";

const llmFields = [
  "enabled",
  "provider",
  "base_url",
  "model",
  "temperature",
  "max_tokens",
  "timeout_seconds",
  "answer_thinking_mode",
  "answer_reasoning_effort",
  "rewrite_enabled",
  "rewrite_thinking_mode",
  "rewrite_reasoning_effort",
  "rewrite_temperature",
  "rewrite_max_tokens",
  "rewrite_timeout_seconds",
] as const;

const retrievalFields = [
  "trace_enabled",
  "mode",
  "final_top_k",
  "dense_candidate_k",
  "dense_distance_threshold",
  "bm25_top_k",
  "bm25_k1",
  "bm25_b",
  "rrf_k",
  "reranker_enabled",
  "reranker_candidate_k",
  "max_chunks_per_document",
] as const;

const chunkingFields = ["strategy", "chunk_size", "chunk_overlap", "max_tokens"] as const;

const visionFields = [
  "enabled",
  "source",
  "provider",
  "base_url",
  "model",
  "pdf_scanned_enabled",
  "pdf_page_images_enabled",
  "embedded_images_enabled",
  "recognize_thinking_mode",
  "recognize_reasoning_effort",
  "recognize_temperature",
  "recognize_max_tokens",
  "recognize_timeout_seconds",
  "describe_thinking_mode",
  "describe_reasoning_effort",
  "describe_temperature",
  "describe_max_tokens",
  "describe_timeout_seconds",
  "max_image_edge",
  "max_calls_per_document",
] as const;

function formValues(settings: SettingsReadResponse): SettingsFormValues {
  return {
    chunking: { ...settings.chunking },
    llm: {
      enabled: settings.llm.enabled,
      provider: settings.llm.provider,
      base_url: settings.llm.base_url,
      model: settings.llm.model,
      temperature: settings.llm.temperature,
      max_tokens: settings.llm.max_tokens,
      timeout_seconds: settings.llm.timeout_seconds,
      answer_thinking_mode: settings.llm.answer_thinking_mode,
      answer_reasoning_effort: settings.llm.answer_reasoning_effort,
      rewrite_enabled: settings.llm.rewrite_enabled,
      rewrite_thinking_mode: settings.llm.rewrite_thinking_mode,
      rewrite_reasoning_effort: settings.llm.rewrite_reasoning_effort,
      rewrite_temperature: settings.llm.rewrite_temperature,
      rewrite_max_tokens: settings.llm.rewrite_max_tokens,
      rewrite_timeout_seconds: settings.llm.rewrite_timeout_seconds,
      api_key_draft: "",
    },
    vision: {
      enabled: settings.vision.enabled,
      source: settings.vision.source,
      provider: settings.vision.provider,
      base_url: settings.vision.base_url,
      model: settings.vision.model,
      pdf_scanned_enabled: settings.vision.pdf_scanned_enabled,
      pdf_page_images_enabled: settings.vision.pdf_page_images_enabled,
      embedded_images_enabled: settings.vision.embedded_images_enabled,
      recognize_thinking_mode: settings.vision.recognize_thinking_mode,
      recognize_reasoning_effort: settings.vision.recognize_reasoning_effort,
      recognize_temperature: settings.vision.recognize_temperature,
      recognize_max_tokens: settings.vision.recognize_max_tokens,
      recognize_timeout_seconds: settings.vision.recognize_timeout_seconds,
      describe_thinking_mode: settings.vision.describe_thinking_mode,
      describe_reasoning_effort: settings.vision.describe_reasoning_effort,
      describe_temperature: settings.vision.describe_temperature,
      describe_max_tokens: settings.vision.describe_max_tokens,
      describe_timeout_seconds: settings.vision.describe_timeout_seconds,
      max_image_edge: settings.vision.max_image_edge,
      max_calls_per_document: settings.vision.max_calls_per_document,
      api_key_draft: "",
    },
    retrieval: {
      trace_enabled: settings.retrieval.trace_enabled,
      mode: settings.retrieval.mode,
      final_top_k: settings.retrieval.final_top_k,
      dense_candidate_k: settings.retrieval.dense_candidate_k,
      dense_distance_threshold: settings.retrieval.dense_distance_threshold,
      bm25_top_k: settings.retrieval.bm25_top_k,
      bm25_k1: settings.retrieval.bm25_k1,
      bm25_b: settings.retrieval.bm25_b,
      rrf_k: settings.retrieval.rrf_k,
      reranker_enabled: settings.retrieval.reranker_enabled,
      reranker_candidate_k: settings.retrieval.reranker_candidate_k,
      max_chunks_per_document: settings.retrieval.max_chunks_per_document,
    },
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "未知错误";
}

function isFormValidationError(error: unknown): boolean {
  return Boolean(
    error && typeof error === "object" && "errorFields" in error,
  );
}

function hasLlmDraftChanges(
  values: SettingsFormValues["llm"] | undefined,
  settings: SettingsReadResponse,
): boolean {
  return Boolean(
    values &&
      llmFields.some(
        (field) =>
          values[field] !== undefined && values[field] !== settings.llm[field],
      ),
  );
}

export default function SettingsPage() {
  const [form] = Form.useForm<SettingsFormValues>();
  const llmDraft = Form.useWatch("llm", form);
  const [settings, setSettings] = useState<SettingsReadResponse | null>(null);
  const [status, setStatus] = useState<SettingsRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [visionTesting, setVisionTesting] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [llmApiKeyIntent, setLlmApiKeyIntent] =
    useState<ApiKeyIntent>("keep");
  const [visionApiKeyIntent, setVisionApiKeyIntent] =
    useState<ApiKeyIntent>("keep");
  const [conflict, setConflict] = useState(false);
  const [feedback, setFeedback] = useState<SettingsPatchResponse | null>(null);
  const [connectionResult, setConnectionResult] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState("retrieval");
  const loadedRef = useRef(false);

  const applyServerSettings = useCallback(
    (next: SettingsReadResponse) => {
      if (!next.chunking || typeof next.retrieval.trace_enabled !== "boolean") {
        throw new Error("设置接口尚未更新，请重启项目后再试。");
      }
      setSettings(next);
      form.setFieldsValue(formValues(next));
      const clearedErrors = form.getFieldsError().map(({ name }) => ({ name, errors: [], warnings: [] }));
      // getFieldsError 返回内部通用路径；这些路径均来自当前强类型表单。
      form.setFields(clearedErrors as unknown as Parameters<typeof form.setFields>[0]);
      setLlmApiKeyIntent("keep");
      setVisionApiKeyIntent("keep");
      setDirty(false);
      setConflict(false);
    },
    [form],
  );

  const reloadSettings = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      applyServerSettings(await api.getSettings());
    } catch (error) {
      setLoadError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [applyServerSettings]);

  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    setStatusError(null);
    try {
      setStatus(await api.getSettingsRuntimeStatus());
    } catch (error) {
      setStatusError(errorMessage(error));
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    void reloadSettings();
    void refreshStatus();
  }, [refreshStatus, reloadSettings]);

  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const guardNavigation = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      const anchor = target?.closest("a[href]") as HTMLAnchorElement | null;
      if (!anchor || anchor.target === "_blank") return;
      if (!window.confirm("当前设置尚未保存，确认离开吗？")) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", guardNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", guardNavigation, true);
    };
  }, [dirty]);

  const setLlmApiKeyIntentState = (intent: ApiKeyIntent) => {
    setLlmApiKeyIntent(intent);
    setDirty(true);
    setFeedback(null);
  };

  const setVisionApiKeyIntentState = (intent: ApiKeyIntent) => {
    setVisionApiKeyIntent(intent);
    setDirty(true);
    setFeedback(null);
  };

  const buildPatch = (values: SettingsFormValues) => {
    if (!settings) return null;
    const llm: LlmSettingsPatch = {};
    for (const field of llmFields) {
      if (
        values.llm[field] !== undefined &&
        values.llm[field] !== settings.llm[field]
      ) {
        (llm as Record<string, unknown>)[field] = values.llm[field];
      }
    }
    if (llmApiKeyIntent === "replace" && values.llm.api_key_draft.trim()) {
      llm.api_key_update = {
        action: "replace",
        value: values.llm.api_key_draft.trim(),
      };
    } else if (llmApiKeyIntent === "clear") {
      llm.api_key_update = { action: "clear" };
    }

    const vision: VisionSettingsPatch = {};
    for (const field of visionFields) {
      if (
        values.vision[field] !== undefined &&
        values.vision[field] !== settings.vision[field]
      ) {
        (vision as Record<string, unknown>)[field] = values.vision[field];
      }
    }
    if (
      visionApiKeyIntent === "replace" &&
      values.vision.api_key_draft.trim()
    ) {
      vision.api_key_update = {
        action: "replace",
        value: values.vision.api_key_draft.trim(),
      };
    } else if (visionApiKeyIntent === "clear") {
      vision.api_key_update = { action: "clear" };
    }

    const retrieval: RetrievalSettingsPatch = {};
    for (const field of retrievalFields) {
      if (
        values.retrieval[field] !== undefined &&
        values.retrieval[field] !== settings.retrieval[field]
      ) {
        (retrieval as Record<string, unknown>)[field] = values.retrieval[field];
      }
    }
    const chunking: ChunkingSettingsPatch = {};
    for (const field of chunkingFields) {
      if (values.chunking[field] !== undefined && values.chunking[field] !== settings.chunking[field]) {
        (chunking as Record<string, unknown>)[field] = values.chunking[field];
      }
    }
    return {
      expected_revision: settings.revision,
      ...(Object.keys(llm).length ? { llm } : {}),
      ...(Object.keys(vision).length ? { vision } : {}),
      ...(Object.keys(retrieval).length ? { retrieval } : {}),
      ...(Object.keys(chunking).length ? { chunking } : {}),
    };
  };

  const handleSave = async () => {
    if (!settings) return;
    try {
      const values = await form.validateFields();
      const patch = buildPatch(values);
      if (!patch || (!patch.llm && !patch.vision && !patch.retrieval && !patch.chunking)) {
        setDirty(false);
        message.info("没有需要保存的修改");
        return;
      }
      setSaving(true);
      setFeedback(null);
      const response = await api.updateSettings(patch);
      applyServerSettings(response.settings);
      setFeedback(response);
      message.success(`设置已保存，当前 revision ${response.revision}`);
      void refreshStatus();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        message.warning("配置已被其他页面更新，请重新加载后再保存");
      } else if (isFormValidationError(error)) {
        // Ant Form 校验错误已经显示在字段旁，不额外弹重复消息。
      } else {
        message.error(`保存失败: ${errorMessage(error)}`);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async () => {
    try {
      await form.validateFields([
        ["llm", "provider"],
        ["llm", "base_url"],
        ["llm", "model"],
        ["llm", "timeout_seconds"],
      ]);
      const values = form.getFieldValue("llm");
      setTesting(true);
      setConnectionResult(null);
      const response = await api.testLlmConnection({
        provider: values.provider,
        base_url: values.base_url,
        model: values.model,
        timeout_seconds: values.timeout_seconds,
        ...(values.api_key_draft.trim()
          ? { api_key: values.api_key_draft.trim() }
          : {}),
      });
      setConnectionResult(
        response.success
          ? `连接成功 · ${response.latency_ms.toFixed(1)} ms · ${response.endpoint}`
          : `${response.message}${response.error_code ? `（${response.error_code}）` : ""}`,
      );
      if (response.success) {
        message.success("模型连接测试成功");
      } else {
        message.error(response.message);
      }
    } catch (error) {
      if (!isFormValidationError(error)) {
        message.error(`连接测试失败: ${errorMessage(error)}`);
      }
    } finally {
      setTesting(false);
    }
  };

  const handleTestVisionConnection = async (task: VisionTestTask) => {
    try {
      const vision = form.getFieldValue(
        "vision",
      ) as SettingsFormValues["vision"];
      const connectionFields =
        vision.source === "custom"
          ? [
              ["vision", "provider"],
              ["vision", "base_url"],
              ["vision", "model"],
            ]
          : [];
      const taskFields =
        task === "recognize"
          ? [
              ["vision", "recognize_thinking_mode"],
              ["vision", "recognize_reasoning_effort"],
              ["vision", "recognize_temperature"],
              ["vision", "recognize_max_tokens"],
              ["vision", "recognize_timeout_seconds"],
            ]
          : [
              ["vision", "describe_thinking_mode"],
              ["vision", "describe_reasoning_effort"],
              ["vision", "describe_temperature"],
              ["vision", "describe_max_tokens"],
              ["vision", "describe_timeout_seconds"],
            ];
      await form.validateFields([...connectionFields, ...taskFields, ["vision", "max_image_edge"]]);

      const taskSettings: VisionSettingsPatch =
        task === "recognize"
          ? {
              recognize_thinking_mode: vision.recognize_thinking_mode,
              recognize_reasoning_effort: vision.recognize_reasoning_effort,
              recognize_temperature: vision.recognize_temperature,
              recognize_max_tokens: vision.recognize_max_tokens,
              recognize_timeout_seconds: vision.recognize_timeout_seconds,
            }
          : {
              describe_thinking_mode: vision.describe_thinking_mode,
              describe_reasoning_effort: vision.describe_reasoning_effort,
              describe_temperature: vision.describe_temperature,
              describe_max_tokens: vision.describe_max_tokens,
              describe_timeout_seconds: vision.describe_timeout_seconds,
            };
      const payloadVision: VisionSettingsPatch = {
        source: vision.source,
        max_image_edge: vision.max_image_edge,
        ...taskSettings,
        ...(vision.source === "custom"
          ? {
              provider: vision.provider,
              base_url: vision.base_url,
              model: vision.model,
            }
          : {}),
      };
      if (vision.source === "custom") {
        if (
          visionApiKeyIntent === "replace" &&
          vision.api_key_draft.trim()
        ) {
          payloadVision.api_key_update = {
            action: "replace",
            value: vision.api_key_draft.trim(),
          };
        } else if (visionApiKeyIntent === "clear") {
          payloadVision.api_key_update = { action: "clear" };
        }
      }

      setVisionTesting(true);
      setConnectionResult(null);
      const response = await api.testVisionConnection({
        vision: payloadVision,
        task,
      });
      const followNote =
        vision.source === "follow_llm" ? " · 使用当前已保存的问答模型" : "";
      setConnectionResult(
        response.success
          ? `视觉连接成功 · ${response.latency_ms.toFixed(1)} ms · ${response.provider} / ${response.model}${followNote} · ${response.endpoint}`
          : `${response.message}${response.error_code ? `（${response.error_code}）` : ""}`,
      );
      if (response.success) {
        message.success("视觉模型连接测试成功");
      } else {
        message.error(response.message);
      }
    } catch (error) {
      if (!isFormValidationError(error)) {
        message.error(`视觉连接测试失败: ${errorMessage(error)}`);
      }
    } finally {
      setVisionTesting(false);
    }
  };

  const handleReset = async () => {
    if (!settings) return;
    setResetting(true);
    setFeedback(null);
    try {
      const response = await api.resetSettings(settings.revision, "all");
      applyServerSettings(response.settings);
      setFeedback(response);
      setResetOpen(false);
      message.success("已恢复部署默认设置");
      void refreshStatus();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        setResetOpen(false);
      }
      message.error(`恢复失败: ${errorMessage(error)}`);
    } finally {
      setResetting(false);
    }
  };

  if (loading && !settings) {
    return (
      <>
        <Form form={form} component={false} />
        <main className="settings-page settings-page-loading">
          <Spin size="large" description="正在读取后端有效设置" />
        </main>
      </>
    );
  }

  if (!settings) {
    return (
      <>
        <Form form={form} component={false} />
        <main className="settings-page">
          <Result
            status="error"
            title="设置加载失败"
            subTitle={loadError || "无法读取后端有效设置"}
            extra={
              <Button icon={<ReloadOutlined />} onClick={() => void reloadSettings()}>
                重试
              </Button>
            }
          />
        </main>
      </>
    );
  }

  return (
    <main className="settings-page">
      <header className="settings-page-header">
        <div>
          <span className="page-eyebrow">工作空间 / 配置中心</span>
          <Typography.Title level={2}>设置</Typography.Title>
          <Typography.Paragraph type="secondary">
            调整检索策略，连接模型，让知识库按你的方式工作。
          </Typography.Paragraph>
        </div>
        <Space orientation="vertical" align="end" size={0}>
          <Typography.Text strong>revision {settings.revision}</Typography.Text>
          <Typography.Text type="secondary">
            {settings.updated_at
              ? `更新于 ${new Date(settings.updated_at).toLocaleString()}`
              : "尚无运行时覆盖"}
          </Typography.Text>
        </Space>
      </header>
      <div className="settings-overview" aria-label="已保存的配置概览">
        <div><FilterOutlined /><span>检索方式<strong>{{ dense: "语义检索", bm25: "关键词检索", hybrid: "混合检索" }[settings.retrieval.mode]}</strong></span></div>
        <div><ApiOutlined /><span>问答模型<strong>{settings.llm.enabled ? settings.llm.model : "仅检索，不生成回答"}</strong></span></div>
        <div><EyeOutlined /><span>视觉解析<strong>{settings.vision.enabled ? "已启用" : "未启用"}</strong></span></div>
      </div>
      {conflict && (
        <Alert
          type="error"
          showIcon
          title="配置已被其他页面更新"
          description="当前草稿基于旧 revision，不能覆盖新配置。请重新加载后再修改。"
          action={
            <Button size="small" danger onClick={() => void reloadSettings()}>
              重新加载
            </Button>
          }
        />
      )}
      {feedback && (
        <Alert
          type="success"
          showIcon
          title={`设置已应用到 revision ${feedback.revision}`}
          description={
            <Space orientation="vertical" size={2}>
              <span>
                {feedback.effects.length
                  ? `实际影响：${feedback.effects.join("、")}`
                  : "有效值未发生变化。"}
              </span>
              {feedback.warnings.map((warning) => (
                <span key={warning}>{warning}</span>
              ))}
            </Space>
          }
          closable
          onClose={() => setFeedback(null)}
        />
      )}
      {connectionResult && (
        <Alert
          type={connectionResult.includes("连接成功") ? "success" : "error"}
          showIcon
          title="连接测试结果"
          description={connectionResult}
          closable
          onClose={() => setConnectionResult(null)}
        />
      )}

      <div className="settings-layout">
        <aside className="settings-sidebar">
          <span className="settings-nav-label">配置导航</span>
          <nav aria-label="设置分区">
            {[
              { id: "retrieval", title: "检索设置", detail: "召回、排序与回答依据", icon: <FilterOutlined /> },
              { id: "chunking", title: "文档分块", detail: "切分方式、长度与重叠", icon: <ScissorOutlined /> },
              { id: "model", title: "问答模型", detail: "连接与生成参数", icon: <ApiOutlined /> },
              { id: "vision", title: "视觉解析", detail: "图片识别与扫描文档", icon: <EyeOutlined /> },
              { id: "diagnostics", title: "诊断与追踪", detail: "检索 Trace 开关", icon: <ExperimentOutlined /> },
              { id: "runtime", title: "运行状态", detail: "本地模型与索引", icon: <DatabaseOutlined /> },
            ].map((section) => (
              <button key={section.id} type="button" aria-current={activeSection === section.id ? "location" : undefined}
                onClick={() => {
                  setActiveSection(section.id);
                  document.getElementById(`settings-${section.id}`)?.scrollIntoView?.({ behavior: "smooth", block: "start" });
                }}>
                {section.icon}<span><strong>{section.title}</strong><small>{section.detail}</small></span>
              </button>
            ))}
          </nav>
          <div className="settings-sidebar-note"><strong>更改何时生效？</strong><p>保存后，问答和检索设置用于下一次提问；文档分块和视觉设置用于下一次上传。</p></div>
          <details className="settings-workspace-note"><summary>本地工作空间说明</summary><p>当前没有账号或权限体系，请勿把设置接口暴露到不受信任网络。设备、模型文件和存储路径是只读启动配置。</p></details>
        </aside>
        <div className="settings-panels">
      <Form<SettingsFormValues>
        form={form}
        layout="vertical"
        requiredMark="optional"
        onValuesChange={() => {
          setDirty(true);
          setFeedback(null);
          setConflict(false);
        }}
      >
        <section id="settings-retrieval" className="settings-anchor-section">
          <RetrievalSettingsCard form={form} settings={settings} />
        </section>
        <section id="settings-chunking" className="settings-anchor-section">
          <ChunkingSettingsCard form={form} settings={settings} />
        </section>
        <section id="settings-model" className="settings-anchor-section">
        <ModelSettingsCard
          form={form}
          settings={settings}
          apiKeyIntent={llmApiKeyIntent}
          testing={testing}
          onApiKeyIntentChange={setLlmApiKeyIntentState}
          onTestConnection={() => void handleTestConnection()}
        />
        </section>
        <section id="settings-vision" className="settings-anchor-section">
        <VisionSettingsCard
          form={form}
          settings={settings}
          apiKeyIntent={visionApiKeyIntent}
          testing={visionTesting}
          llmDirty={
            llmApiKeyIntent !== "keep" ||
            hasLlmDraftChanges(llmDraft, settings)
          }
          onApiKeyIntentChange={setVisionApiKeyIntentState}
          onTestConnection={(task) => void handleTestVisionConnection(task)}
        />
        </section>
        <section id="settings-diagnostics" className="settings-anchor-section">
          <DiagnosticsSettingsCard settings={settings} />
        </section>
      </Form>
      <section id="settings-runtime" className="settings-anchor-section">
      <RuntimeStatusCard
        status={status}
        loading={statusLoading}
        error={statusError}
        onRefresh={() => void refreshStatus()}
      />
      </section>
        </div>
      </div>

      <SettingsApplyBar
        revision={settings.revision}
        dirty={dirty}
        saving={saving}
        resetting={resetting}
        onDiscard={() => applyServerSettings(settings)}
        onReset={() => setResetOpen(true)}
        onSave={() => void handleSave()}
      />

      <Modal
        open={resetOpen}
        title="恢复全部部署默认？"
        okText="确认恢复"
        cancelText="取消"
        confirmLoading={resetting}
        okButtonProps={{ danger: true }}
        onOk={() => void handleReset()}
        onCancel={() => !resetting && setResetOpen(false)}
      >
        <Typography.Paragraph>
          这会删除问答模型、文档分块、视觉解析、检索设置和 API Key 的页面运行时覆盖，重新使用部署默认值，并产生新的 revision。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary">
          不会修改 .env，不会重建或清空向量数据库。
        </Typography.Paragraph>
      </Modal>
    </main>
  );
}
