import type { InferSelectModel } from "drizzle-orm";
import { index, integer, pgTable, text, timestamp, varchar } from "drizzle-orm/pg-core";

/**
 * A holographic-pet generation task. The frontend creates one after an upload,
 * then polls it. The actual video model + hardware dispatch run in an EXTERNAL
 * backend, which will update `status` / `videoUrl` via the task API. Until that
 * external system is connected, the task API advances progress server-side from
 * `startedAt` so the waiting UI has a real, database-backed source of truth.
 */
export const generationTasks = pgTable(
  "generation_tasks",
  {
    id: varchar("id", { length: 64 }).primaryKey(),
    // Uploaded photo reference (object-storage URL or opaque image id).
    imageUrl: text("image_url"),
    imageId: varchar("image_id", { length: 128 }),
    // pending | processing | success | failed
    status: varchar("status", { length: 24 }).notNull().default("pending"),
    stage: varchar("stage", { length: 32 }).notNull().default("queued"),
    progress: integer("progress").notNull().default(0),
    // Estimated total duration (ms) used to derive progress before the
    // external model reports real status.
    durationMs: integer("duration_ms").notNull().default(14000),
    videoUrl: text("video_url"),
    error: text("error"),
    startedAt: timestamp("started_at").notNull().defaultNow(),
    updatedAt: timestamp("updated_at").notNull().defaultNow(),
  },
  (table) => ({
    statusIdx: index("generation_tasks_status_idx").on(table.status),
    startedAtIdx: index("generation_tasks_started_at_idx").on(table.startedAt),
  })
);

export type GenerationTask = InferSelectModel<typeof generationTasks>;
