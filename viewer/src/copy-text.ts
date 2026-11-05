/**
 * Clipboard for opaque, sandboxed HTML viewers without a clipboard grant. Call
 * inside the user gesture: the legacy copy runs synchronously, before any await.
 * Returns whether a copy was reported, so callers never claim success without one.
 */
export async function copyText(text: string): Promise<boolean> {
  const field = document.createElement("textarea");
  const focused = document.activeElement as HTMLElement | null;
  field.value = text;
  field.readOnly = true;
  field.setAttribute("aria-hidden", "true");
  Object.assign(field.style, { position: "fixed", left: "0", top: "0", width: "1px", height: "1px", opacity: "0", pointerEvents: "none" });
  document.body.append(field);
  let copied = false;
  try { field.focus({ preventScroll: true }); field.select(); copied = document.execCommand("copy"); } catch { /* Fall through to the async API. */ }
  finally { field.remove(); focused?.focus({ preventScroll: true }); }
  if (copied) return true;
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}
