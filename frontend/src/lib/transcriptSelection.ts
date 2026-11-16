// Pure helpers behind the live transcript's batch selection (pick phrases to
// discard, e.g. everything recorded after a meeting someone forgot to stop).

/** Toggle one id, or, with an anchor, set the whole anchor..target range. */
export function clickSelect(
  order: readonly number[],
  selected: ReadonlySet<number>,
  target: number,
  anchor: number | null,
): Set<number> {
  const next = new Set(selected);
  const from = anchor == null ? -1 : order.indexOf(anchor);
  const to = order.indexOf(target);
  if (from < 0 || to < 0) {
    if (next.has(target)) next.delete(target);
    else next.add(target);
    return next;
  }
  // A shift-click mirrors the anchor's state onto the range, like file lists.
  const on = selected.has(anchor as number);
  const [lo, hi] = from <= to ? [from, to] : [to, from];
  for (const id of order.slice(lo, hi + 1)) {
    if (on) next.add(id);
    else next.delete(id);
  }
  return next;
}

/** Add `id` and every phrase after it. */
export function selectFrom(
  order: readonly number[],
  selected: ReadonlySet<number>,
  id: number,
): Set<number> {
  const i = order.indexOf(id);
  const next = new Set(selected);
  if (i < 0) return next;
  for (const later of order.slice(i)) next.add(later);
  return next;
}

/** Everything not currently selected. */
export function invertSelection(
  order: readonly number[],
  selected: ReadonlySet<number>,
): Set<number> {
  return new Set(order.filter((id) => !selected.has(id)));
}

/**
 * Where a list index lands once some items are removed: the count of kept items
 * before it. Keeps index-based markers (recording boundaries, analysis and
 * translation cursors) pointing at the same phrase after a discard.
 */
export function shiftIndex(
  order: readonly number[],
  removed: ReadonlySet<number>,
  index: number,
): number {
  let kept = 0;
  for (const id of order.slice(0, index)) if (!removed.has(id)) kept += 1;
  return kept;
}

/** Re-point index markers after a discard, dropping ones that now coincide. */
export function shiftMarkers<T extends { index: number }>(
  order: readonly number[],
  removed: ReadonlySet<number>,
  markers: readonly T[],
): T[] {
  const kept = order.length - order.filter((id) => removed.has(id)).length;
  const out: T[] = [];
  for (const m of markers) {
    const index = shiftIndex(order, removed, m.index);
    if (index <= 0 || index >= kept || out.some((o) => o.index === index)) continue;
    out.push({ ...m, index });
  }
  return out;
}
