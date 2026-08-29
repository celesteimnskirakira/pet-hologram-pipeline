import { type NextRequest, NextResponse } from "next/server";
import { pollGenerationTask, updateGenerationTask } from "@/lib/db/queries";

/**
 * GET /api/generation/:taskId
 * Returns the current status of a generation task, advancing derived progress.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ taskId: string }> }
) {
  const { taskId } = await params;
  const task = await pollGenerationTask(taskId);
  if (!task) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  return NextResponse.json({
    taskId: task.id,
    status: task.status,
    stage: task.stage,
    progress: task.progress,
    videoUrl: task.videoUrl ?? undefined,
  });
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
  const { taskId } = await params;
  let body: {
    status?: unknown;
    stage?: unknown;
    progress?: unknown;
    videoUrl?: unknown;
    error?: unknown;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }

  const patch: Parameters<typeof updateGenerationTask>[1] = {};
  if (typeof body.status === "string") patch.status = body.status;
  if (typeof body.stage === "string") patch.stage = body.stage as never;
  if (typeof body.progress === "number") patch.progress = body.progress;
  if (typeof body.videoUrl === "string") patch.videoUrl = body.videoUrl;
  if (typeof body.error === "string") patch.error = body.error;

  const task = await updateGenerationTask(taskId, patch);
  if (!task) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  return NextResponse.json({ ok: true, taskId: task.id, status: task.status });
}
