import { NextResponse } from "next/server";
import { listGenerationTasks } from "@/lib/db/queries";

// Local-only operator feed. Put behind operator auth before LAN/public use.
export async function GET() {
  const tasks = await listGenerationTasks();
  return NextResponse.json({
    jobs: tasks.map((task) => ({
      taskId: task.id,
      displayCode: task.displayCode ?? task.id.slice(-6).toUpperCase(),
      status: task.status,
      stage: task.stage,
      progress: task.progress,
      selectedAction: task.selectedAction,
      deliveryStatus:
        task.deliveryStatus ??
        (task.status === "completed" || task.status === "success"
          ? "ready"
          : null),
      updatedAt: task.updatedAt.toISOString(),
    })),
  });
}
