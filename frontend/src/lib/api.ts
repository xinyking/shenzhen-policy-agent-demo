export type * from "../types";

export async function uploadScreenshot(file: File): Promise<string> {
  // Materialize the selected file before transport; a detached file handle must
  // fail here instead of leaving a request waiting on a file-backed body.
  const bytes = await file.arrayBuffer();
  const response = await fetch("/v1/screenshots", { method: "POST", headers: { "Content-Type": file.type, "X-Policy-Client": "workspace" }, body: bytes });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message || "截图上传未成功");
  return value.screenshot_id;
}
export async function api<T>(
  path: string,
  method = "GET",
  data?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Policy-Client": "workspace",
    },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(value.error?.message || `请求失败 (${response.status})`);
  return value;
}
export function download(name: string, text: string) {
  const url = URL.createObjectURL(
    new Blob([text], { type: "text/plain;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function official(url: string) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" &&
      (u.hostname === "sz.gov.cn" || u.hostname.endsWith(".sz.gov.cn"))
      ? url
      : undefined;
  } catch {
    return undefined;
  }
}
