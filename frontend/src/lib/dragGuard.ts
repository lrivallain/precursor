// An <iframe> (draw.io, HTML/PDF previews) swallows mousemove/mouseup once the
// cursor crosses it, so a resize drag over one would stall and never end. While
// any drag is live, index.css turns pointer events off on every iframe.
const CLASS = "resizing";
let active = 0;

export function beginDrag(): () => void {
  if (typeof document === "undefined") return () => undefined;
  active += 1;
  document.documentElement.classList.add(CLASS);
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    active -= 1;
    if (active <= 0) {
      active = 0;
      document.documentElement.classList.remove(CLASS);
    }
  };
}
