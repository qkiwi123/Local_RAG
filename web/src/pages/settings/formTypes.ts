import type { LlmSettings, RetrievalSettings, VisionSettings } from "../../api/types";

export interface SettingsFormValues {
  llm: Omit<LlmSettings, "api_key"> & { api_key_draft: string };
  vision: Omit<VisionSettings, "api_key" | "effective_connection"> & {
    api_key_draft: string;
  };
  retrieval: Omit<RetrievalSettings, "refusal_gate">;
}

export type ApiKeyIntent = "keep" | "replace" | "clear";
