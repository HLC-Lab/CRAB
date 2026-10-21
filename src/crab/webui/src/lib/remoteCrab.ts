/** How a remote's "Crab command" field reads: hint text, install panel, save payload. */

/** Where the CRAB checkout lives for an install dir (matches the backend). */
export function crabDir(installDir: string | null | undefined): string {
  return `${(installDir || "~").replace(/\/+$/, "")}/CRAB`;
}

export interface CrabCommandView {
  /** Hint under the field: the checkout location when empty, a note when set. */
  hint: string;
  /** The guided "Install CRAB" panel only applies to a checkout, not to a user-managed command. */
  showInstall: boolean;
  /** A client-side message when the value cannot be saved, else null. */
  error: string | null;
  /** What to send to the backend: the trimmed command, or null (never ""). */
  payload: string | null;
}

export function crabCommandNote(command: string): string {
  return `This cluster runs \`${command}\` directly. You manage that install (pipx install crab-hpc).`;
}

export function crabCommandView(
  input: string | null | undefined,
  installDir: string | null | undefined,
): CrabCommandView {
  const value = (input ?? "").trim();
  if (!value) {
    return {
      hint: `Empty: the CRAB checkout in ${crabDir(installDir)}`,
      showInstall: true,
      error: null,
      payload: null,
    };
  }
  return {
    hint: crabCommandNote(value),
    showInstall: false,
    error: /\s/.test(value)
      ? "Crab command must be one executable path or name with no spaces (for example ~/.local/bin/crab)."
      : null,
    payload: value,
  };
}
