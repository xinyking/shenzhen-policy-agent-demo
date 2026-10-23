import { useEffect, useRef } from "react";
import { api } from "../lib/api";
import type { RunEvent, RunStatus, Result } from "../types";
const terminal = new Set(["completed", "degraded", "needs_input", "failed", "cancelled", "interrupted"]);

export function useDeepAgentSession() {
 const subscription=useRef<AbortController | null>(null);
 useEffect(()=>()=>subscription.current?.abort(),[]);
 return subscription;
}

// SSE reconnects automatically with Last-Event-ID; polling covers interrupted connections.
export function waitForRun(runId: string, onEvent: (event: RunEvent) => void, signal: AbortSignal): Promise<Result> {
  return new Promise((resolve, reject) => {
    let closed = false, checking = false, cursor = 0;
    const stream = new EventSource(`/v1/runs/${encodeURIComponent(runId)}/events`);
    function close() {
      closed = true; stream.close(); clearInterval(timer); clearTimeout(deadline);
      signal.removeEventListener("abort", abort);
    }
    function abort() { close(); reject(new Error("状态订阅已关闭；任务仍可在刷新后恢复查看。")); }
    async function check() {
      if (checking || closed) return;
      checking = true;
      try {
        const status = await api<RunStatus>(`/v1/runs/${runId}`);
        if (closed) return;
        if (terminal.has(status.status)) {
          if (status.result_available) {
            const result = await api<Result>(`/v1/runs/${runId}/result`);
            if (!closed) { close(); resolve(result); }
          } else { close(); reject(new Error(status.error || "任务未产生结果")); }
        }
      } catch (e) {
        // A short network interruption should not discard a running task. The deadline stays bounded.
        if ((e as Error).message.includes("404") && !closed) { close(); reject(e); }
      } finally { checking = false; }
    }
    stream.onmessage = (message) => {
      if (closed) return;
      try {
        const event = JSON.parse(message.data) as RunEvent;
        if (event.run_id !== runId || !Number.isInteger(event.seq) || event.seq <= cursor) return;
        cursor = event.seq; onEvent(event);
        if (event.type === "run_finished") void check();
      } catch { /* Ignore malformed transport events; status endpoint remains authoritative. */ }
    };
    stream.onerror = () => { void check(); };
    const timer = setInterval(() => { void check(); }, 2000);
    const deadline = setTimeout(() => { close(); reject(new Error("状态连接超时，请刷新页面重新查看该任务。")); }, 240_000);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort(); else void check();
  });
}
