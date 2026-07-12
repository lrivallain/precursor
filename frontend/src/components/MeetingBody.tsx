import { useMemo } from "react";
import DOMPurify from "dompurify";

/**
 * Render untrusted meeting-body HTML (from a calendar invite) safely. DOMPurify
 * strips scripts/handlers; links open in a new tab. Kept in a scrollable,
 * prose-styled box since invite bodies can be long.
 */
export function MeetingBody({ html }: { html: string }) {
  const clean = useMemo(
    () =>
      DOMPurify.sanitize(html, {
        ALLOWED_ATTR: ["href", "src", "alt", "title", "target", "rel"],
        FORBID_TAGS: ["style", "script", "iframe"],
      }),
    [html],
  );
  return (
    <div
      className="prose prose-sm dark:prose-invert max-h-64 max-w-none overflow-y-auto rounded border border-border bg-bg px-3 py-2 text-[13px] [&_a]:text-accent [&_img]:max-w-full"
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  );
}
