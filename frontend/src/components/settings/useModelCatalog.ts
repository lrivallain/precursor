import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { modelsStore } from "../../lib/modelsStore";
import { settingsStore } from "../../lib/settingsStore";
import type { LLMModel } from "../../lib/types";

/**
 * The model catalog of the provider being configured. Only the latest request
 * lands, so switching providers quickly never shows a stale list.
 */
export function useModelCatalog() {
  const [models, setModels] = useState<LLMModel[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const request = useRef(0);
  useEffect(() => () => { request.current++; }, []);

  // `share` seeds the shared store used by the composer picker — only safe when
  // the fetched list reflects the *saved* provider, not an unsaved preview.
  async function loadModels(provider: string, { share = false }: { share?: boolean } = {}): Promise<LLMModel[]> {
    const current = ++request.current;
    setModelsLoading(true);
    setModelsError(null);
    setModels([]);
    try {
      const list = await api.llm.listModels(provider);
      if (current !== request.current) return list;
      setModels(list);
      modelsStore.adopt(list, provider);
      if (share) modelsStore.applySettings(settingsStore.current());
      return list;
    } catch (e) {
      if (current === request.current) {
        setModels([]);
        setModelsError(e instanceof Error ? e.message : String(e));
      }
      return [];
    } finally {
      if (current === request.current) setModelsLoading(false);
    }
  }

  return { models, modelsError, setModelsError, modelsLoading, setModelsLoading, loadModels };
}

/** The catalog of the global default provider, for the default-model picker. */
export function useProviderModels(provider: string): LLMModel[] {
  const [models, setModels] = useState<LLMModel[]>([]);
  useEffect(() => {
    if (!provider) return;
    let stale = false;
    setModels([]);
    api.llm.listModels(provider).then(
      (list) => { if (!stale) setModels(list); },
      () => {},
    );
    return () => { stale = true; };
  }, [provider]);
  return models;
}
