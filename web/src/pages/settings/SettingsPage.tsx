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
import { ReloadOutlined } from "@ant-design/icons";
import { api, ApiError } from "../../api/client";
import type {
  LlmSettingsPatch,
  RetrievalSettingsPatch,
  SettingsPatchResponse,
  SettingsReadResponse,
  SettingsRuntimeStatus,
} from "../../api/types";
import type { ApiKeyIntent, SettingsFormValues } from "./formTypes";
import { ModelSettingsCard } from "./ModelSettingsCard";
import { RetrievalSettingsCard } from "./RetrievalSettingsCard";
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

function formValues(settings: SettingsReadResponse): SettingsFormValues {
  return {
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
    retrieval: {
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

export default function SettingsPage() {
  const [form] = Form.useForm<SettingsFormValues>();
  const [settings, setSettings] = useState<SettingsReadResponse | null>(null);
  const [status, setStatus] = useState<SettingsRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [apiKeyIntent, setApiKeyIntentState] = useState<ApiKeyIntent>("keep");
  const [conflict, setConflict] = useState(false);
  const [feedback, setFeedback] = useState<SettingsPatchResponse | null>(null);
  const [connectionResult, setConnectionResult] = useState<string | null>(null);
  const loadedRef = useRef(false);

  const applyServerSettings = useCallback(
    (next: SettingsReadResponse) => {
      setSettings(next);
      form.setFieldsValue(formValues(next));
      setApiKeyIntentState("keep");
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

  const setApiKeyIntent = (intent: ApiKeyIntent) => {
    setApiKeyIntentState(intent);
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
    if (apiKeyIntent === "replace" && values.llm.api_key_draft.trim()) {
      llm.api_key_update = {
        action: "replace",
        value: values.llm.api_key_draft.trim(),
      };
    } else if (apiKeyIntent === "clear") {
      llm.api_key_update = { action: "clear" };
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
    return {
      expected_revision: settings.revision,
      ...(Object.keys(llm).length ? { llm } : {}),
      ...(Object.keys(retrieval).length ? { retrieval } : {}),
    };
  };

  const handleSave = async () => {
    if (!settings) return;
    try {
      const values = await form.validateFields();
      const patch = buildPatch(values);
      if (!patch || (!patch.llm && !patch.retrieval)) {
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
          <Typography.Title level={2}>设置</Typography.Title>
          <Typography.Paragraph type="secondary">
            管理全局问答模型与检索参数。页面读取后端有效值，不使用 localStorage 保存配置。
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

      <Alert
        type="warning"
        showIcon
        title="可信本地工作空间设置"
        description="当前没有账号或权限体系，请勿把设置接口直接暴露到不受信任网络。设备、模型文件、存储路径和 chunk 策略仍为只读启动配置。"
      />
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
          type={connectionResult.startsWith("连接成功") ? "success" : "error"}
          showIcon
          title="连接测试结果"
          description={connectionResult}
          closable
          onClose={() => setConnectionResult(null)}
        />
      )}

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
        <ModelSettingsCard
          form={form}
          settings={settings}
          apiKeyIntent={apiKeyIntent}
          testing={testing}
          onApiKeyIntentChange={setApiKeyIntent}
          onTestConnection={() => void handleTestConnection()}
        />
        <RetrievalSettingsCard form={form} settings={settings} />
      </Form>

      <RuntimeStatusCard
        status={status}
        loading={statusLoading}
        error={statusError}
        onRefresh={() => void refreshStatus()}
      />

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
          这会删除 LLM、API Key 和 Retrieval 的页面运行时覆盖，重新使用环境变量和代码默认值，并产生新的 revision。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary">
          不会修改 .env，不会重建或清空向量数据库。
        </Typography.Paragraph>
      </Modal>
    </main>
  );
}
