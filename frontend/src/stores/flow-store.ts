"use client";

import { useSyncExternalStore } from "react";

interface PettaFlowState {
  photoUrl: string | null;
  photoFile: File | null;
  imageId: string | null;
  taskId: string | null;
  displayCode: string | null;
  selectedAction: string | null;
  videoUrl: string | null;
}

let state: PettaFlowState = {
  photoUrl: null,
  photoFile: null,
  imageId: null,
  taskId: null,
  displayCode: null,
  selectedAction: null,
  videoUrl: null,
};

const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function setState(patch: Partial<PettaFlowState>) {
  state = { ...state, ...patch };
  emit();
}

export const flowStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot() {
    return state;
  },
  setPhoto(file: File, url: string) {
    if (state.photoUrl) URL.revokeObjectURL(state.photoUrl);
    setState({ photoFile: file, photoUrl: url });
  },
  clearPhoto() {
    if (state.photoUrl) URL.revokeObjectURL(state.photoUrl);
    setState({ photoFile: null, photoUrl: null, imageId: null });
  },
  setImageId(id: string) {
    setState({ imageId: id });
  },
  setTaskId(id: string) {
    setState({ taskId: id });
  },
  setDisplayCode(code: string) {
    setState({ displayCode: code });
  },
  setCompletion(details: { selectedAction?: string; videoUrl?: string; displayCode?: string }) {
    setState({
      selectedAction: details.selectedAction ?? state.selectedAction,
      videoUrl: details.videoUrl ?? state.videoUrl,
      displayCode: details.displayCode ?? state.displayCode,
    });
  },
  reset() {
    if (state.photoUrl) URL.revokeObjectURL(state.photoUrl);
    setState({
      photoUrl: null,
      photoFile: null,
      imageId: null,
      taskId: null,
      displayCode: null,
      selectedAction: null,
      videoUrl: null,
    });
  },
};

const SERVER_SNAPSHOT: PettaFlowState = {
  photoUrl: null,
  photoFile: null,
  imageId: null,
  taskId: null,
  displayCode: null,
  selectedAction: null,
  videoUrl: null,
};

export function useFlow(): PettaFlowState {
  return useSyncExternalStore(
    flowStore.subscribe,
    flowStore.getSnapshot,
    () => SERVER_SNAPSHOT,
  );
}
