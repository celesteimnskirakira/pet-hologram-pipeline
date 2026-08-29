import { createHash, timingSafeEqual } from "node:crypto";
import { type NextRequest, NextResponse } from "next/server";
import { getGenerationTask, updateGenerationTask } from "@/lib/db/queries";

const CALLBACK_SECRET = process.env.GENERATION_CALLBACK_SECRET ?? "";
const ALLOWED_STATUSES = new Set([
  "queued",
  "processing",
  "completed",
  "failed",
  "delivery_failed",
  "expired",
  "cancelled",
]);
const ALLOWED_STAGES = new Set([
  "queued",
  "validating",
  "generating_still",
  "generating_video",
  "post_processing",
  "delivering",
  "completed",
  "failed",
  "delivery_failed",
  "expired",
  "cancelled",
]);
const ALLOWED_ACTIONS = new Set(["舔毛", "走路", "睡觉", "挠脖子"]);

function secretMatches(candidate: string, expected: string): boolean {
  if (!expected) return false;
  const left = createHash("sha256").update(candidate).digest();
  const right = createHash("sha256").update(expected).digest();
  return timingSafeEqual(left, right);
}

/**
 * GET /api/generation/:taskId
 * Returns the current database state without deriving progress from elapsed time.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ taskId: string }> }
) {
  const { taskId } = await params;
  const task = await getGenerationTask(taskId);
  if (!task) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  return NextResponse.json({
    taskId: task.id,
    status: task.status,
    stage: task.stage,
    progress: task.progress,
    videoUrl: task.videoUrl ?? undefined,
    message: task.message ?? undefined,
    errorCode: task.errorCode ?? undefined,
    error: task.error ?? undefined,
    selectedAction: task.selectedAction ?? undefined,
    displayCode: task.displayCode ?? undefined,
    deliveryStatus: task.deliveryStatus ?? undefined,
    artifacts: parseArtifacts(task.artifacts),
    updatedAt: task.updatedAt.toISOString(),
  });
}

function parseArtifacts(value: string | null): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * PATCH /api/generation/:taskId
 * Reserved for the EXTERNAL generation backend to report terminal state /
 * failure / real clip URL once the video model + hardware dispatch complete.
 */
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ taskId: string }> }
) {
  const suppliedSecret = request.headers.get("x-generation-callback-secret") ?? "";
  if (!secretMatches(suppliedSecret, CALLBACK_SECRET)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const { taskId } = await params;
  let body: {
    status?: unknown;
    stage?: unknown;
    progress?: unknown;
    videoUrl?: unknown;
    error?: unknown;
    errorCode?: unknown;
    message?: unknown;
    artifacts?: unknown;
    selectedAction?: unknown;
    displayCode?: unknown;
    deliveryStatus?: unknown;
    job_id?: unknown;
    video_url?: unknown;
    error_code?: unknown;
    selected_action?: unknown;
    display_code?: unknown;
    delivery_status?: unknown;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }

  if (body.job_id !== undefined && body.job_id !== taskId) {
    return NextResponse.json({ error: "task_id_mismatch" }, { status: 400 });
  }
  const normalized = {
    ...body,
    videoUrl: body.videoUrl ?? body.video_url,
    errorCode: body.errorCode ?? body.error_code,
    selectedAction: body.selectedAction ?? body.selected_action,
    displayCode: body.displayCode ?? body.display_code,
    deliveryStatus: body.deliveryStatus ?? body.delivery_status,
  };

  if (
    (normalized.status !== undefined &&
      (typeof normalized.status !== "string" || !ALLOWED_STATUSES.has(normalized.status))) ||
    (normalized.stage !== undefined &&
      (typeof normalized.stage !== "string" || !ALLOWED_STAGES.has(normalized.stage))) ||
    (normalized.progress !== undefined &&
      (typeof normalized.progress !== "number" ||
        !Number.isInteger(normalized.progress) ||
        normalized.progress < 0 ||
        normalized.progress > 100)) ||
    (normalized.errorCode !== undefined &&
      (typeof normalized.errorCode !== "string" || !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(normalized.errorCode))) ||
    (normalized.selectedAction !== undefined &&
      (typeof normalized.selectedAction !== "string" || !ALLOWED_ACTIONS.has(normalized.selectedAction))) ||
    (normalized.displayCode !== undefined &&
      (typeof normalized.displayCode !== "string" || !/^\d{6}$/.test(normalized.displayCode)))
  ) {
    return NextResponse.json({ error: "invalid_patch" }, { status: 400 });
  }

  const patch: Parameters<typeof updateGenerationTask>[1] = {};
  if (typeof normalized.status === "string") patch.status = normalized.status;
  if (typeof normalized.stage === "string") patch.stage = normalized.stage as never;
  if (typeof normalized.progress === "number") patch.progress = normalized.progress;
  if (typeof normalized.videoUrl === "string") patch.videoUrl = normalized.videoUrl;
  if (typeof normalized.error === "string") patch.error = normalized.error;
  if (typeof normalized.errorCode === "string") patch.errorCode = normalized.errorCode;
  if (typeof normalized.message === "string") patch.message = normalized.message;
  if (normalized.artifacts && typeof normalized.artifacts === "object") patch.artifacts = JSON.stringify(normalized.artifacts);
  if (typeof normalized.selectedAction === "string") patch.selectedAction = normalized.selectedAction;
  if (typeof normalized.displayCode === "string") patch.displayCode = normalized.displayCode;
  if (typeof normalized.deliveryStatus === "string") patch.deliveryStatus = normalized.deliveryStatus;

  const task = await updateGenerationTask(taskId, patch);
  if (!task) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  return NextResponse.json({ ok: true, taskId: task.id, status: task.status });
}
