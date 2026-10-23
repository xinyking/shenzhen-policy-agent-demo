import { useState } from "react";
import { MoreHorizontal, Pencil, Trash2, X } from "lucide-react";
import type { Consultation } from "../lib/storage";

export default function HistoryItem({ consultation, current, busy, onOpen, onRename, onDelete }: {
  consultation: Consultation; current: boolean; busy: boolean;
  onOpen: () => void; onRename: (title: string) => void; onDelete: () => void;
}) {
  const [action, setAction] = useState<"" | "menu" | "rename" | "delete">("");
  const [title, setTitle] = useState(consultation.title);
  return <div className="history-item" onKeyDown={(event) => {
    if (event.key === "Escape") { event.stopPropagation(); setAction(""); }
  }}>
    <div className={`history-row ${current ? "current" : ""}`}>
      <button onClick={() => { setAction(""); onOpen(); }} title={consultation.title}>{consultation.title}</button>
      <button className="icon" aria-label={`管理咨询 ${consultation.title}`} aria-expanded={Boolean(action)}
        onClick={() => setAction(action ? "" : "menu")}><MoreHorizontal size={16} /></button>
    </div>
    {action === "menu" && <div className="history-actions" aria-label={`咨询操作 ${consultation.title}`}>
      <button onClick={() => { setTitle(consultation.title); setAction("rename"); }}><Pencil size={14} />重命名</button>
      <button className="danger-action" disabled={busy} onClick={() => setAction("delete")}><Trash2 size={14} />删除咨询</button>
      <button className="icon" aria-label="关闭咨询操作" onClick={() => setAction("")}><X size={14} /></button>
      {busy && <small>查询完成后可删除</small>}
    </div>}
    {action === "rename" && <form className="history-editor" onSubmit={(event) => {
      event.preventDefault(); if (!title.trim()) return;
      onRename(title.trim()); setAction("");
    }}>
      <label>咨询名称<input aria-label="新的咨询名称" autoFocus maxLength={100} value={title}
        onChange={(event) => setTitle(event.target.value)} /></label>
      <div className="actions"><button type="submit" disabled={!title.trim()}>保存名称</button>
        <button type="button" onClick={() => setAction("")}>取消</button></div>
    </form>}
    {action === "delete" && <div className="history-editor" role="group" aria-label="确认删除咨询">
      <p>删除“{consultation.title}”及其中{consultation.turns.length}条问答？手动日程保留。此操作无法撤销。</p>
      <div className="actions"><button className="danger-action" disabled={busy} onClick={() => { onDelete(); setAction(""); }}>确认删除</button>
        <button onClick={() => setAction("")}>取消</button></div>
    </div>}
  </div>;
}
