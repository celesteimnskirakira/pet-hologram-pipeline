/**
 * Deprecated module. The generation flow now lives in the real backend:
 *   - typed client: `src/lib/api/generation.ts` (re-exported from `@/lib/api`)
 *   - API routes:   `src/app/api/generation/*`
 *   - persistence:  `src/lib/db/{schema,queries}/generation-tasks.ts`
 *
 * Kept only as a thin re-export so any stale import resolves to the real API.
 */
export {
  uploadPhoto,
  createGeneration,
  getGeneration,
  type GenerationStage,
  type GenerationStatus,
  type TaskState,
  type UploadResult,
} from "@/lib/api/generation";
