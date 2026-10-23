import type { Result, RunEvent, MaterialDraft } from "./api";
export type Consultation = {
  id: string;
  title: string;
  created: string;
  turns: { id?: string; question: string; context?: string; result?: Result; error?: string; runId?: string; runEvents?: RunEvent[]; screenshotName?: string }[];
};
export type Schedule = {
  id: string;
  title: string;
  date: string;
  notes: string;
  materials?: string;
  applicationMethod?: string;
  done: boolean;
  consultationId?: string;
  created: string;
  applicationStatus?: "planned" | "preparing" | "submitted" | "approved" | "rejected";
  materialsChecklist?: (MaterialDraft & { done: boolean })[];
  policySources?: { evidence_id: string; title: string; source_url: string; version: string; locator: string }[];
};
export type LocalData = {
  consultations: Consultation[];
  schedules: Schedule[];
  profile: string;
};
export const empty: LocalData = {
  consultations: [],
  schedules: [],
  profile: "",
};
const db = new Promise<IDBDatabase>((resolve, reject) => {
  const request = indexedDB.open("shenzhen-policy-personal", 1);
  request.onupgradeneeded = () => request.result.createObjectStore("workspace");
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});
export async function load(): Promise<LocalData> {
  const database = await db;
  return new Promise((resolve, reject) => {
    const req = database
      .transaction("workspace")
      .objectStore("workspace")
      .get("data");
    req.onsuccess = () => resolve(req.result || { ...empty });
    req.onerror = () => reject(req.error);
  });
}
export async function save(data: LocalData) {
  const database = await db;
  return new Promise<void>((resolve, reject) => {
    const tx = database.transaction("workspace", "readwrite");
    tx.objectStore("workspace").put(data, "data");
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}
