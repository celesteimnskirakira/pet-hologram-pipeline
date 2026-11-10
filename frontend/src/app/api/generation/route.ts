import { type NextRequest, NextResponse } from "next/server";
import { createGenerationTask } from "@/lib/db/queries";

const GENERATION_BACKEND_URL =
  process.env.GENERATION_BACKEND_URL ?? "https://genpichong.dpdns.org";
const CALLBACK_BASE_URL = process.env.GENERATION_CALLBACK_BASE_URL ?? "";
const GENERATION_BACKEND_SECRET = process.env.GENERATION_BACKEND_SECRET ?? "";

/**
 * POST /api/generation
 * Creates a holographic-pet generation task for an uploaded photo.
 * Body: { imageId?: string; imageUrl?: string }
 * The frontend persists the task before submitting it to the Python service so
 * authenticated callbacks survive page reloads and remain auditable.
 */
export async function POST(request: NextRequest) {
  if (!GENERATION_BACKEND_SECRET || !CALLBACK_BASE_URL) {
    return NextResponse.json({ error: "generation_backend_not_configured" }, { status: 503 });
  }
  let callbackBase: URL;
  try {
    callbackBase = new URL(CALLBACK_BASE_URL);
    if (callbackBase.protocol !== "https:" || callbackBase.pathname !== "/") throw new Error();
  } catch {
    return NextResponse.json({ error: "generation_callback_url_invalid" }, { status: 503 });
  }
  let body: { imageId?: unknown; imageUrl?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }

  const imageId = typeof body.imageId === "string" ? body.imageId : null;
  const imageUrl = typeof body.imageUrl === "string" ? body.imageUrl : null;

  if (!imageUrl) {
    return NextResponse.json({ error: "image_required" }, { status: 400 });
  }

  const task = await createGenerationTask({ imageId, imageUrl });
  const callbackUrl = new URL(`/api/generation/${task.id}`, callbackBase).toString();
  try {
    const response = await fetch(`${GENERATION_BACKEND_URL.replace(/\/$/, "")}/api/v1/jobs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-generation-backend-secret": GENERATION_BACKEND_SECRET,
      },
      body: JSON.stringify({
        task_id: task.id,
        image_id: imageId,
        image_url: imageUrl,
        callback_url: callbackUrl,
        display_code: task.displayCode,
      }),
      cache: "no-store",
    });
    if (response.status !== 202) throw new Error(`generation_backend_${response.status}`);
    const accepted = (await response.json()) as { job_id?: unknown; display_code?: unknown };
    if (accepted.job_id !== task.id || accepted.display_code !== task.displayCode) {
      throw new Error("generation_backend_invalid_response");
    }
  } catch (error) {
    console.error("generation backend unavailable", error);
    await import("@/lib/db/queries").then(({ updateGenerationTask }) =>
      updateGenerationTask(task.id, {
        status: "failed",
        stage: "failed",
        progress: 0,
        message: "生成服务暂时不可用",
        errorCode: "generation_backend_unavailable",
        error: error instanceof Error ? error.message : String(error),
      })
    );
    return NextResponse.json({ error: "generation_backend_unavailable" }, { status: 503 });
  }

  return NextResponse.json(
    { taskId: task.id, displayCode: task.displayCode },
    { status: 202 }
  );
}
