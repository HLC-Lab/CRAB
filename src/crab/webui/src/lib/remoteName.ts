// A remote's name is its key in the profile store: the backend refuses a second remote with the
// same name (409). The form checks it first, so the refusal shows at the Name field.

/** Whether `name` belongs to another remote. `editing` is the remote being edited, or null. */
export function nameTaken(
  items: readonly { name: string }[],
  name: string,
  editing: string | null,
): boolean {
  if (!name) return false;
  return items.some((item) => item.name === name && item.name !== editing);
}

/** The hint under the Name field for a taken name. */
export function takenMessage(name: string): string {
  return `A remote named '${name}' already exists. Choose another name.`;
}
