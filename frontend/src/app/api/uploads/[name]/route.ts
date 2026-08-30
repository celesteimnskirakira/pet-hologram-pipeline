import { type NextRequest, NextResponse } from "next/server";
import { authorizePetUpload, readPetUpload } from "@/lib/uploads";

export const runtime = "nodejs";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  const expires = request.nextUrl.searchParams.get("expires") || "";
  const signature = request.nextUrl.searchParams.get("signature") || "";
  try {
    if (!authorizePetUpload(name, expires, signature)) {
      return NextResponse.json({ error: "invalid_or_expired_upload_url" }, { status: 403 });
    }
    const upload = await readPetUpload(name);
    return new NextResponse(new Uint8Array(upload.data), {
      headers: {
        "content-type": upload.contentType,
        "content-length": String(upload.data.length),
        "cache-control": "private, no-store",
        "x-content-type-options": "nosniff",
      },
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }
    console.error("upload read failed", error);
    return NextResponse.json({ error: "upload_read_failed" }, { status: 500 });
  }
}
