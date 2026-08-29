import { storage } from "@eazo/sdk";
import { request } from "./request";

export type GenerationStage =
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

export type GenerationStatus = "queued" | "processing" | "completed" | "success" | "failed" | "delivery_failed" | "expired" | "cancelled";

export interface TaskState {
  taskId: string;
  status: GenerationStatus;
  progress: number;
  stage: GenerationStage;
  videoUrl?: string;
  message?: string;
  errorCode?: string;
  error?: string;
  selectedAction?: string;
  displayCode?: string;
  deliveryStatus?: string;
  artifacts?: Record<string, unknown>;
  updatedAt?: string;
}

export interface UploadResult {
  imageId: string;
  imageUrl: string;
}

/**
 * Uploads the pet photo directly to Eazo object storage (S3 via presigned URL)
 * and returns its permanent CDN url. No file bytes pass through our backend.
 */
export async function uploadPhoto(file: File): Promise<UploadResult> {
  const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_") || "photo";
  const path = `pet-photos/${Date.now()}-${safeName}`;
  const { key, url } = await storage.upload(path, file);
  return { imageId: key, imageUrl: url };
}

/** Creates a generation task for an uploaded photo; returns the task id. */
export async function createGeneration(input: {
  imageId: string;
  imageUrl: string;
}): Promise<{ taskId: string; displayCode?: string }> {
  const res = await request("/api/generation", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error("create_generation_failed");
  return (await res.json()) as { taskId: string; displayCode?: string };
}

/** Polls a generation task's current status. */
export async function getGeneration(taskId: string): Promise<TaskState> {
  const res = await request(`/api/generation/${taskId}`);
  if (!res.ok) throw new Error("poll_generation_failed");
  return (await res.json()) as TaskState;
}
