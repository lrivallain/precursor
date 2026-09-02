import { createContentLoader } from "vitepress";

/**
 * One entry in the plugin catalogue.
 *
 * The catalogue's source of truth is `website/plugins/<id>.md`: its frontmatter
 * is the metadata, its body is the documentation page. The same files are
 * bundled into the Python package (`precursor/catalog`) and parsed by
 * `precursor.backend.plugins.catalog`, so the site and the app never disagree
 * about what is listed — keep the two schemas in step.
 *
 * This loader lives outside `website/plugins/` on purpose: that directory is
 * copied verbatim into the wheel, so it stays pure markdown.
 */
export interface CatalogPlugin {
  /** Entry-point name, which is also the file name and the page slug. */
  id: string;
  title: string;
  summary: string;
  /** PyPI project name — the only thing ever handed to an installer. */
  distribution: string;
  homepage: string | null;
  author: string | null;
  license: string | null;
  tags: string[];
  contributes: string[];
  recommended: boolean;
  /** Path of this entry's own documentation page. */
  url: string;
}

declare const data: CatalogPlugin[];
export { data };

export default createContentLoader("plugins/*.md", {
  transform(raw): CatalogPlugin[] {
    return raw
      // A page is a catalogue entry *iff* it declares a distribution, which is
      // what lets the index and the submission guide sit in the same folder as
      // ordinary pages. The backend applies the identical rule.
      .filter((page) => typeof page.frontmatter.distribution === "string")
      .map((page) => ({
        id: page.url.split("/").pop() || "",
        title: page.frontmatter.title ?? "",
        summary: page.frontmatter.description ?? "",
        distribution: page.frontmatter.distribution,
        homepage: page.frontmatter.homepage ?? null,
        author: page.frontmatter.author ?? null,
        license: page.frontmatter.license ?? null,
        tags: page.frontmatter.tags ?? [],
        contributes: page.frontmatter.contributes ?? [],
        recommended: page.frontmatter.recommended === true,
        url: page.url,
      }))
      // Recommended first, then alphabetical — the same order the app renders.
      .sort(
        (a, b) =>
          Number(b.recommended) - Number(a.recommended) ||
          a.title.localeCompare(b.title),
      );
  },
});
