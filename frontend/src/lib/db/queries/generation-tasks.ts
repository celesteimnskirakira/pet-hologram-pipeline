import { randomInt, randomUUID } from "node:crypto";
import { eq } from "drizzle-orm";
import { db } from "../client";
import { generationTasks, type GenerationTask } from "../schema/generation-tasks";

export type TaskStage =
  | "queued"
  | "validating"
  | "generating_still"
  | "generating_video"
  | "post_processing"
  | "delivering"
  | "completed"
  | "failed"
  | "delivery_failed"
  | "expired"
  | "cancelled";

function randomId(prefix: string): string {
  return `${prefix}_${randomUUID().replaceAll("-", "")}`;
}

function randomDisplayCode(): string {
  return String(randomInt(0, 1_000_000)).padStart(6, "0");
}

export async function createGenerationTask(input: {
  imageId?: string | null;
  imageUrl?: string | null;
}): Promise<GenerationTask> {
  const rows = await db
    .insert(generationTasks)
    .values({
      id: randomId("task"),
      imageId: input.imageId ?? null,
      imageUrl: input.imageUrl ?? null,
      status: "queued",
      stage: "queued",
      progress: 0,
      displayCode: randomDisplayCode(),
      startedAt: new Date(),
    })
    .returning();
  return rows[0];
}

export async function getGenerationTask(
  id: string
): Promise<GenerationTask | undefined> {
  const rows = await db
    .select()
    .from(generationTasks)
    .where(eq(generationTasks.id, id))
    .limit(1);
  return rows[0];
}

export async function listGenerationTasks(limit = 100): Promise<GenerationTask[]> {
  return db.select().from(generationTasks).limit(Math.max(1, Math.min(limit, 500)));
}

/** Applies a status update received from the authenticated generation backend. */
export async function updateGenerationTask(
  id: string,
  patch: {
    status?: string;
    stage?: TaskStage;
    progress?: number;
    videoUrl?: string | null;
    error?: string | null;
    message?: string | null;
    errorCode?: string | null;
    selectedAction?: string | null;
    artifacts?: string | null;
    displayCode?: string | null;
    deliveryStatus?: string | null;
  }
): Promise<GenerationTask | undefined> {
  const rows = await db
    .update(generationTasks)
    .set({ ...patch, updatedAt: new Date() })
    .where(eq(generationTasks.id, id))
    .returning();
  return rows[0];
}
