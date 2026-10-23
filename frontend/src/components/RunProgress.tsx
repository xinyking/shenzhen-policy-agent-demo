import { useEffect, useState } from "react";
import type { RunEvent } from "../lib/api";

const labels: Record<string, string> = {
  web_search: "本地暂无结果，联网查找官方原文",
  check_store: "检查本地政策库", load_memory: "检查可选会话记忆", classify: "判断政策领域",
  retrieve: "检索官方政策原文", decide: "研究资料与查询缺口", tool: "补查政策章节",
  generate: "整理完整答复", review: "检查回答引用", schedule: "准备待确认日程草案",
  fallback: "整理可用原文", finish: "完成来源检查",
  read_screenshot: "识别办事截图（待你核对）", prepare_evidence: "补充相关原文章节", compare_materials: "核对截图材料与政策",
};

export default function RunProgress({ events, busy, onCancel, cancelling = false }:
  { events: RunEvent[]; busy: boolean; onCancel?: () => void; cancelling?: boolean }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  const starts = events.filter((e) => e.type === "node_started");
  const first = events[0];
  const last = events.at(-1);
  const elapsed = first ? Math.max(0, Math.floor(((busy ? now : Date.parse(last?.at || first.at)) - Date.parse(first.at)) / 1000)) : 0;
  const content = <ol className="run-steps">
    {starts.map((start) => {
      const end = events.find((e) => e.seq > start.seq && e.node === start.node &&
        (e.type === "node_completed" || e.type === "node_failed"));
      return <li key={start.seq} className={end ? (end.type === "node_failed" ? "step-failed" : "step-done") : busy ? "step-active" : "step-stopped"}>
        <span className="step-dot" aria-hidden="true">{end ? end.type === "node_failed" ? "!" : "✓" : busy ? "·" : "–"}</span>
        <span>{labels[start.node || ""] || "处理任务"}
          {end?.data.source_count !== undefined && <small>找到 {end.data.source_count} 条原文证据</small>}
        </span>
        <small>{end?.data.elapsed_ms !== undefined ? `${(end.data.elapsed_ms / 1000).toFixed(1)}s` : busy ? "进行中" : "已停止"}</small>
      </li>;
    })}
    {!starts.length && <li>任务已提交，等待开始…</li>}
  </ol>;
  return <section className="run-progress" aria-label="真实执行状态">
    <div className="run-progress-heading"><strong>{busy ? cancelling ? "正在取消" : "正在查询政策" : "本次执行记录"}</strong><small>{elapsed}s</small>
      {busy && onCancel && <button className="text-button" disabled={cancelling} onClick={onCancel}>{cancelling ? "取消中…" : "停止"}</button>}
    </div>
    {busy ? content : <details><summary>查看执行步骤</summary>{content}</details>}
    {busy && <p className="small muted" role="status">{cancelling ? "正在停止后端任务，请稍候。" : "这里展示实际执行状态；完整答案将在检查完成后一次性显示。"}</p>}
    {events.some((e) => e.type === "repair_requested") && <p className="small muted">执行决策或回答格式需要修复，已尝试重新整理。</p>}
    {events.some((e) => e.type === "fallback_used") && <p className="small muted">部分步骤未成功，已使用可用替代方式。</p>}
    {events.some((e) => e.type === "research_concluded") && <p className="small muted">补查已结束，优先完成现有证据的回答与引用核查。</p>}
    {events.some((e) => e.type === "memory_save_failed") && <p className="small muted">本轮结果已完成，但会话记忆保存失败。</p>}
  </section>;
}
