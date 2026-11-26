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

/** Hint under "Install dir". In SbatchMan mode the folder is the umbrella install (it holds
 * `benchmarks/`, the configs file and the CRAB checkout). Elsewhere it is where the CRAB checkout
 * lives; Save only stores the profile, the "Install CRAB" panel on the cluster's card installs.
 * `usesCheckout` is false when a Crab command is set: then CRAB is not run from this folder. */
export function installDirHint(
  installDir: string | null | undefined,
  sbatchman: boolean,
  usesCheckout: boolean,
): string {
  if (sbatchman) {
    const base =
      "The umbrella folder (crab-sbatchman) on the cluster, already installed there with ./install.sh.";
    return usesCheckout ? `${base} CRAB is in ${crabDir(installDir)}.` : base;
  }
  if (!usesCheckout) return "";
  return `CRAB is in ${crabDir(installDir)}. Save only stores this profile: the folder must already hold CRAB, or use Install CRAB on the cluster's card after saving.`;
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
      hint: `Empty: uses the CRAB checkout in ${crabDir(installDir)}. Fill this only for a CRAB you installed yourself, for example ~/.local/bin/crab.`,
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
