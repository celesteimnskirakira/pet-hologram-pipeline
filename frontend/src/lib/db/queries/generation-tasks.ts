import { eq } from "drizzle-orm";
import { db } from "../client";
import { generationTasks, type GenerationTask } from "../schema/generation-tasks";

export type TaskStage =
  | "queued"
  | "uploading"
  | "creating_task"
  | "generating_video"
  | "sending_hardware"
  | "success"
  | "failed";

function stageForProgress(p: number): TaskStage {
  if (p >= 100) return "success";
  if (p >= 78) return "sending_hardware";
  if (p >= 22) return "generating_video";
  if (p >= 8) return "creating_task";
  return "uploading";
}

function randomId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
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
      status: "processing",
      stage: "uploading",
      progress: 0,
      durationMs: 14000,
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

/**
 * Reads a task and advances its derived progress. Once the EXTERNAL video model
 * is connected it will PATCH the row to a terminal state; until then progress is
 * derived from elapsed time so polling returns a real, monotonic, DB-backed
 * value instead of a client-side simulation.
 */
export async function pollGenerationTask(
  id: string
): Promise<GenerationTask | undefined> {
  const task = await getGenerationTask(id);
  if (!task) return undefined;
  if (task.status === "success" || task.status === "failed") return task;

  const elapsed = Date.now() - new Date(task.startedAt).getTime();
  const raw = Math.min(100, Math.round((elapsed / task.durationMs) * 100));
  const eased = Math.min(100, Math.round(100 * Math.pow(raw / 100, 0.85)));

  if (eased >= 100) {
    const rows = await db
      .update(generationTasks)
      .set({
        status: "success",
        stage: "success",
        progress: 100,
        // Placeholder handle for the clip the external backend produces.
        videoUrl: task.videoUrl ?? `petta://clip/${task.id}`,
        updatedAt: new Date(),
      })
      .where(eq(generationTasks.id, id))
      .returning();
    return rows[0];
  }

  const rows = await db
    .update(generationTasks)
    .set({
      progress: eased,
      stage: stageForProgress(eased),
      status: "processing",
      updatedAt: new Date(),
    })
    .where(eq(generationTasks.id, id))
    .returning();
  return rows[0];
}

/** Reserved for the external backend to report a terminal state or failure. */
export async function updateGenerationTask(
  id: string,
  patch: {
    status?: string;
    stage?: TaskStage;
    progress?: number;
    videoUrl?: string | null;
    error?: string | null;
  }
): Promise<GenerationTask | undefined> {
  const rows = await db
    .update(generationTasks)
    .set({ ...patch, updatedAt: new Date() })
    .where(eq(generationTasks.id, id))
    .returning();
  return rows[0];
}
