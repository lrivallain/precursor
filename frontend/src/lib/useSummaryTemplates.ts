import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type { MeetingSummaryOptions, SummaryTemplateCatalog } from "./types";

export interface SummaryTemplatesState {
  catalog: SummaryTemplateCatalog | null;
  /** The picked template's id ("" until the catalog loads). */
  template: string;
  /** The picked language tag; "" writes in the session's language. */
  language: string;
  setTemplate: (id: string) => void;
  setLanguage: (code: string) => void;
  /** Re-read the templates, e.g. after a template file was edited. */
  refresh: () => Promise<void>;
  /** What to send with a generation: empty until the catalog loads, so the
   * backend falls back to the last used. */
  options: MeetingSummaryOptions;
}

/**
 * The Summary tab's template + language pickers. They start from what was last
 * used (server-side, so it follows the user across sessions and browsers), and
 * each pick is remembered for the next recap.
 */
export function useSummaryTemplates(): SummaryTemplatesState {
  const [catalog, setCatalog] = useState<SummaryTemplateCatalog | null>(null);
  const [template, setTemplateState] = useState("");
  const [language, setLanguageState] = useState("");
  const seeded = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await api.meetings.summaryTemplates();
      setCatalog(next);
      if (!seeded.current) {
        seeded.current = true;
        setTemplateState(next.last_template);
        setLanguageState(next.last_language);
      } else {
        // The picked template's file may have gone (or broken) meanwhile.
        setTemplateState((current) =>
          next.templates.some((t) => t.id === current) ? current : next.last_template,
        );
      }
    } catch {
      /* non-fatal — generation falls back to the last used server-side */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const remember = useCallback((nextTemplate: string, nextLanguage: string) => {
    if (!nextTemplate) return;
    void api.meetings.selectSummaryTemplate(nextTemplate, nextLanguage).catch(() => {});
  }, []);

  const setTemplate = useCallback(
    (id: string) => {
      setTemplateState(id);
      remember(id, language);
    },
    [language, remember],
  );

  const setLanguage = useCallback(
    (code: string) => {
      setLanguageState(code);
      remember(template, code);
    },
    [template, remember],
  );

  const options = useMemo<MeetingSummaryOptions>(
    () => (catalog && template ? { template, language } : {}),
    [catalog, template, language],
  );

  return { catalog, template, language, setTemplate, setLanguage, refresh, options };
}
