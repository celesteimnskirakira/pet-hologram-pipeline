"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { AmbientShell } from "@/components/petta/ambient-shell";
import { PettaOrbLoader } from "@/components/petta/orb-loader";
import { HoloCaptureFrame } from "@/components/petta/holo-capture-frame";
import { uploadPhoto, createGeneration } from "@/lib/api";
import { flowStore, useFlow } from "@/stores/flow-store";

export default function UploadPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const flow = useFlow();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(false);

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    flowStore.setPhoto(file, url);
    setError(false);
  }

  async function onGenerate() {
    if (!flow.photoFile) return;
    setUploading(true);
    setError(false);
    try {
      const { imageId, imageUrl } = await uploadPhoto(flow.photoFile);
      flowStore.setImageId(imageId);
      const { taskId, displayCode } = await createGeneration({ imageId, imageUrl });
      flowStore.setTaskId(taskId);
      if (displayCode) flowStore.setDisplayCode(displayCode);
      router.push("/generating");
    } catch {
      setUploading(false);
      setError(true);
    }
  }

  return (
    <AmbientShell scatter={false}>
      {/* Header */}
      <div className="flex items-center justify-between" data-el="upload-header">
        <button
          type="button"
          onClick={() => router.push("/")}
          disabled={uploading}
          className="grid h-10 w-10 place-items-center rounded-full border border-[var(--petta-line)] bg-[var(--petta-glass)] text-[var(--petta-cream)] backdrop-blur-md disabled:opacity-40"
          aria-label={t("petta.upload.back")}
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <span className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--petta-muted)]">
          petta
        </span>
        <span className="h-10 w-10" />
      </div>

      {/* Stage */}
      <section className="flex flex-1 flex-col items-center justify-center gap-6 text-center" data-el="upload-stage">
        <h2
          className="m-0 text-white"
          style={{ fontSize: "clamp(26px,8vw,36px)", lineHeight: 1.1, letterSpacing: "-0.03em", fontWeight: 560 }}
        >
          {uploading ? t("petta.upload.processing") : t("petta.upload.title")}
        </h2>

        {uploading ? (
          <PettaOrbLoader size={230} photoUrl={flow.photoUrl} />
        ) : (
          <>
            <input
              ref={inputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={onPick}
            />
            <HoloCaptureFrame
              photoUrl={flow.photoUrl}
              label={t("petta.upload.choose")}
              hint={t("petta.upload.hint")}
              onClick={() => inputRef.current?.click()}
            />
          </>
        )}

        {error && (
          <p className="text-sm text-[var(--petta-peach)]" data-el="upload-error">
            {t("petta.upload.errorTitle")}
          </p>
        )}
      </section>

      {/* Action */}
      <footer data-el="upload-actions">
        {flow.photoUrl && !uploading ? (
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="min-h-[54px] flex-1 rounded-full border border-[var(--petta-line)] bg-[var(--petta-glass)] text-base font-semibold text-[var(--petta-cream)] backdrop-blur-md transition-transform active:scale-[.98]"
              data-el="upload-retake"
            >
              {t("petta.upload.retake")}
            </button>
            <button
              type="button"
              onClick={onGenerate}
              disabled={!flow.photoFile}
              className="min-h-[54px] flex-1 rounded-full bg-[var(--petta-cream)] text-base font-bold text-[#23170f] transition-transform active:scale-[.98] disabled:opacity-40"
              data-el="upload-generate"
            >
              {error ? t("petta.upload.errorRetry") : t("petta.upload.generate")}
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={onGenerate}
            disabled={!flow.photoFile || uploading}
            className="min-h-[54px] w-full rounded-full bg-[var(--petta-cream)] text-base font-bold text-[#23170f] transition-transform active:scale-[.98] disabled:opacity-40"
            data-el="upload-generate"
          >
            {error ? t("petta.upload.errorRetry") : t("petta.upload.generate")}
          </button>
        )}
      </footer>
    </AmbientShell>
  );
}
