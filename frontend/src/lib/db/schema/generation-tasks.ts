import type { InferSelectModel } from "drizzle-orm";
import { index, integer, pgTable, text, timestamp, varchar } from "drizzle-orm/pg-core";

/**
 * A holographic-pet generation task. The frontend creates one after an upload,
 * then polls it. The actual video model + hardware dispatch run in an EXTERNAL
 * backend, which updates status, progress and artifacts through the authenticated
 * callback API. Reads never derive or advance progress from elapsed time.
 */
export const generationTasks = pgTable(
  "generation_tasks",
  {
    id: varchar("id", { length: 64 }).primaryKey(),
    // Uploaded photo reference (object-storage URL or opaque image id).
    imageUrl: text("image_url"),
    imageId: varchar("image_id", { length: 128 }),
    // queued | processing | completed | failed | delivery_failed | expired | cancelled
    status: varchar("status", { length: 24 }).notNull().default("pending"),
    stage: varchar("stage", { length: 32 }).notNull().default("queued"),
    progress: integer("progress").notNull().default(0),
    videoUrl: text("video_url"),
    error: text("error"),
    message: text("message"),
    errorCode: varchar("error_code", { length: 64 }),
    selectedAction: varchar("selected_action", { length: 32 }),
    artifacts: text("artifacts"),
    displayCode: varchar("display_code", { length: 6 }),
    deliveryStatus: varchar("delivery_status", { length: 32 }),
    startedAt: timestamp("started_at").notNull().defaultNow(),
    updatedAt: timestamp("updated_at").notNull().defaultNow(),
  },
  (table) => ({
    statusIdx: index("generation_tasks_status_idx").on(table.status),
    startedAtIdx: index("generation_tasks_started_at_idx").on(table.startedAt),
  })
);

export type GenerationTask = InferSelectModel<typeof generationTasks>;
