import { type NextRequest, NextResponse } from "next/server";
import { createGenerationTask } from "@/lib/db/queries";

/**
 * POST /api/generation
 * Creates a holographic-pet generation task for an uploaded photo.
 * Body: { imageId?: string; imageUrl?: string }
 * The actual video model + hardware dispatch run in an external backend, which
 * will later report terminal status back onto this task row.
 */
export async function POST(request: NextRequest) {
  let body: { imageId?: unknown; imageUrl?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }

  const imageId = typeof body.imageId === "string" ? body.imageId : null;
  const imageUrl = typeof body.imageUrl === "string" ? body.imageUrl : null;

  const task = await createGenerationTask({ imageId, imageUrl });
  return NextResponse.json({ taskId: task.id });
}
