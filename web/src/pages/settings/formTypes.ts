import type { LlmSettings, RetrievalSettings } from "../../api/types";

export interface SettingsFormValues {
  llm: Omit<LlmSettings, "api_key"> & { api_key_draft: string };
  retrieval: Omit<RetrievalSettings, "refusal_gate">;
}

export type ApiKeyIntent = "keep" | "replace" | "clear";
