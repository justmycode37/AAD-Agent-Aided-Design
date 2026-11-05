export type DisplayMode = "shaded-edges" | "shaded" | "wireframe" | "transparent";
export type Visibility = [number, number];

/** Change presentation without restoring parts the user deliberately hid. */
export function displayStates(states: Record<string, Visibility>, mode: DisplayMode): Record<string, Visibility> {
  return Object.fromEntries(Object.entries(states).map(([id, state]) => {
    if (!state.includes(1)) return [id, [...state]];
    const faces = mode === "wireframe" ? 0 : 1;
    const edges = mode === "shaded" || mode === "transparent" ? 0 : 1;
    return [id, [state[0] === 3 ? 3 : faces, state[1] === 3 ? 3 : edges]];
  }));
}
