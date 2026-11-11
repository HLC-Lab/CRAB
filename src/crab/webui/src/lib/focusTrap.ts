// Pure keyboard rules for dialogs (ModalDialog.vue applies them to the DOM).
// Kept free of DOM types so vitest can test them in the node environment.

/**
 * Index to focus after Tab (or Shift+Tab when `backwards`) among `count`
 * focusable elements, when `current` is the focused one's index. Wraps at both
 * ends. A `current` outside the list (-1: focus is on the dialog itself or
 * outside it) enters from the matching end. Returns -1 when `count` is 0.
 */
export function nextFocusIndex(current: number, count: number, backwards: boolean): number {
  if (count <= 0) return -1;
  if (current < 0 || current >= count) return backwards ? count - 1 : 0;
  if (backwards) return current === 0 ? count - 1 : current - 1;
  return current === count - 1 ? 0 : current + 1;
}

/** True for the key name that closes a dialog ("Esc" is the legacy spelling). */
export function isEscape(key: string): boolean {
  return key === "Escape" || key === "Esc";
}
