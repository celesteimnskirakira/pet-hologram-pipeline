import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { mkdir, open, readFile } from "node:fs/promises";

const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const DEFAULT_TTL_SECONDS = 2 * 60 * 60;
const FILE_PATTERN = /^[a-f0-9]{32}\.(jpg|png|webp)$/;
const UPLOAD_DIRECTORY = "/var/lib/petta/frontend/uploads";

const IMAGE_FORMATS = [
  {
    extension: "jpg",
    contentType: "image/jpeg",
    matches: (data: Buffer) =>
      data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff,
  },
  {
    extension: "png",
    contentType: "image/png",
    matches: (data: Buffer) =>
      data.length >= 8 && data.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex")),
  },
  {
    extension: "webp",
    contentType: "image/webp",
    matches: (data: Buffer) =>
      data.length >= 12 &&
      data.subarray(0, 4).toString("ascii") === "RIFF" &&
      data.subarray(8, 12).toString("ascii") === "WEBP",
  },
] as const;

function signingSecret(): string {
  const value = process.env.PETTA_UPLOAD_SIGNING_SECRET || "";
  if (value.length < 32) throw new Error("PETTA_UPLOAD_SIGNING_SECRET is not configured");
  return value;
}

function publicAppUrl(): URL {
  const value = new URL(process.env.PUBLIC_APP_URL || "");
  if (value.protocol !== "https:" || value.pathname !== "/") {
    throw new Error("PUBLIC_APP_URL must be an HTTPS origin");
  }
  return value;
}

function signature(name: string, expires: number): string {
  return createHmac("sha256", signingSecret()).update(`${name}:${expires}`).digest("hex");
}

export async function savePetUpload(file: File): Promise<{ key: string; url: string }> {
  if (file.size <= 0 || file.size > MAX_UPLOAD_BYTES) {
    throw new Error("invalid_file_size");
  }
  const data = Buffer.from(await file.arrayBuffer());
  const format = IMAGE_FORMATS.find((candidate) => candidate.matches(data));
  if (!format) throw new Error("unsupported_image_format");

  const name = `${randomUUID().replaceAll("-", "")}.${format.extension}`;
  await mkdir(UPLOAD_DIRECTORY, { recursive: true, mode: 0o750 });
  const handle = await open(
    `${UPLOAD_DIRECTORY}/${name}`,
    "wx",
    0o640
  );
  try {
    await handle.writeFile(data);
  } finally {
    await handle.close();
  }

  const ttl = Math.max(300, Number(process.env.PETTA_UPLOAD_URL_TTL_SECONDS) || DEFAULT_TTL_SECONDS);
  const expires = Math.floor(Date.now() / 1000) + ttl;
  const url = new URL(`/api/uploads/${name}`, publicAppUrl());
  url.searchParams.set("expires", String(expires));
  url.searchParams.set("signature", signature(name, expires));
  return { key: name, url: url.toString() };
}

export function authorizePetUpload(name: string, expiresValue: string, candidate: string): boolean {
  if (!FILE_PATTERN.test(name) || !/^\d{10}$/.test(expiresValue)) return false;
  const expires = Number(expiresValue);
  if (!Number.isSafeInteger(expires) || expires < Math.floor(Date.now() / 1000)) return false;
  const expected = signature(name, expires);
  if (!/^[a-f0-9]{64}$/.test(candidate)) return false;
  return timingSafeEqual(Buffer.from(candidate, "hex"), Buffer.from(expected, "hex"));
}

export async function readPetUpload(name: string): Promise<{ data: Buffer; contentType: string }> {
  if (!FILE_PATTERN.test(name)) throw new Error("invalid_upload_name");
  const extension = name.split(".").pop();
  const format = IMAGE_FORMATS.find((candidate) => candidate.extension === extension);
  if (!format) throw new Error("unsupported_image_format");
  return {
    data: await readFile(`${UPLOAD_DIRECTORY}/${name}`),
    contentType: format.contentType,
  };
}
