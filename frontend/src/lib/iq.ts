import type { IQField, IQHit, SearchField, SearchResult, SearchSection } from "./types";

/**
 * An IQ hit on something the app can open. IQ also returns memory entries,
 * which have no page to jump to, so those are left out.
 */
export interface IQNavHit {
  section: SearchSection;
  field: IQField;
  is_title: boolean;
  entity_id: number;
  ref: string | null;
  title: string;
  snippet: string;
  role: string | null;
  updated_at: string | null;
}

// Entity kinds the app can open (memory has no page).
export const NAVIGABLE_SECTIONS: SearchSection[] = ["topics", "chats", "agents", "live"];

export function navigableHit(hit: IQHit): IQNavHit | null {
  if (hit.section === "memory") return null;
  return { ...hit, section: hit.section };
}

// The opener only cares about section + entity; fold IQ-only fields onto the
// nearest legacy one so the shared `SearchResult` shape still holds.
export function toSearchResult(hit: IQNavHit): SearchResult {
  const field: SearchField =
    hit.field === "brief"
      ? "summary"
      : hit.field === "attachment" || hit.field === "memory"
        ? "message"
        : hit.field;
  return { ...hit, field };
}

// Answers cite sources as `[^n]`; show them as plain `[n]` markers that line
// up with a numbered source list.
export function plainCitations(answer: string): string {
  return answer.replace(/\[\^(\d+)\]/g, "[$1]");
}

/** Anchor prefix for citation links, so a click handler can resolve them. */
export const CITATION_HREF = "#iq-source-";

// Turn `[^n]` into a Markdown link to `#iq-source-n`, so a citation can be
// clicked to open its source.
export function linkedCitations(answer: string): string {
  return answer.replace(/\[\^(\d+)\]/g, `[[$1]](${CITATION_HREF}$1)`);
}
