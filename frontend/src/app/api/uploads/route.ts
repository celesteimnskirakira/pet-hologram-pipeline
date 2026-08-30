import { type NextRequest, NextResponse } from "next/server";
import { savePetUpload } from "@/lib/uploads";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    const form = await request.formData();
    const file = form.get("file");
    if (!(file instanceof File)) {
      return NextResponse.json({ error: "image_required" }, { status: 400 });
    }
    const upload = await savePetUpload(file);
    return NextResponse.json(upload, { status: 201 });
  } catch (error) {
    const code = error instanceof Error ? error.message : "upload_failed";
    const status = code === "invalid_file_size" || code === "unsupported_image_format" ? 400 : 500;
    if (status === 500) console.error("upload failed", error);
    return NextResponse.json({ error: code }, { status });
  }
}
