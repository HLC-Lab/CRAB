// TS mirror of the backend contract (Pydantic models / CLI --json shapes).
// Hand-kept in sync with src/crab/web/*; consider code-gen later.

/** Stable error envelope returned by the backend (see web/errors.py). */
export interface ErrorEnvelope {
  code: string;
  message: string;
  detail?: string;
}

/** GET /api/health */
export interface Health {
  status: "ok";
  crab_version: string;
  api_schema: number;
}
