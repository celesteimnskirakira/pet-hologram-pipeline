"use client";

import { useSyncExternalStore } from "react";

interface PettaFlowState {
  photoUrl: string | null;
  photoFile: File | null;
  imageId: string | null;
  taskId: string | null;
}

let state: PettaFlowState = {
  photoUrl: null,
  photoFile: null,
  imageId: null,
  taskId: null,
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
  reset() {
    if (state.photoUrl) URL.revokeObjectURL(state.photoUrl);
    setState({
      photoUrl: null,
      photoFile: null,
      imageId: null,
      taskId: null,
    });
  },
};

const SERVER_SNAPSHOT: PettaFlowState = {
  photoUrl: null,
  photoFile: null,
  imageId: null,
  taskId: null,
};

export function useFlow(): PettaFlowState {
  return useSyncExternalStore(
    flowStore.subscribe,
    flowStore.getSnapshot,
    () => SERVER_SNAPSHOT,
  );
}
