import type { LLMModel } from "./types";

// VS Code requires both limits on a Custom Endpoint model; these stand in when
// the provider doesn't advertise them (true of most non-Copilot catalogues).
const DEFAULT_MAX_INPUT_TOKENS = 128_000;
const DEFAULT_MAX_OUTPUT_TOKENS = 16_384;

/**
 * A `chatLanguageModels.json` array registering Precursor as a VS Code
 * "Custom Endpoint" model provider.
 *
 * Models are listed explicitly rather than discovered from the endpoint's URL:
 * VS Code's Custom Endpoint discovery only keeps model ids it already knows the
 * capabilities of, so a discovered Precursor catalogue comes back empty.
 */
export function vscodeLanguageModelsConfig(
  baseUrl: string,
  apiKey: string,
  models: LLMModel[],
): string {
  const entry = {
    name: "Precursor",
    vendor: "customendpoint",
    apiKey,
    apiType: "chat-completions",
    models: models.map((m) => {
      const efforts = m.supported_reasoning_efforts ?? [];
      return {
        id: m.id,
        name: m.name,
        url: `${baseUrl}/chat/completions`,
        // Agent mode hides models without tool calling, and the endpoint
        // relays tools for every provider it serves.
        toolCalling: true,
        vision: !!m.vision,
        maxInputTokens: m.context_window ?? DEFAULT_MAX_INPUT_TOKENS,
        maxOutputTokens: m.max_output_tokens ?? DEFAULT_MAX_OUTPUT_TOKENS,
        ...(efforts.length > 0
          ? {
              thinking: true,
              supportsReasoningEffort: efforts,
              reasoningEffortFormat: "chat-completions",
            }
          : {}),
      };
    }),
  };
  return JSON.stringify([entry], null, 2);
}
