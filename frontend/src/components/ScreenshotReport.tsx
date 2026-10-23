import { useState } from "react";
import { official, type Result, type ScheduleDraft } from "../lib/api";

const requirements = { required: "截图所示：必需", conditional: "截图所示：特定情形", optional: "截图所示：可选", unclear: "要求待核实" };

export default function ScreenshotReport({ result, onSchedule }:
  { result: Result; onSchedule: (draft?: ScheduleDraft) => void | Promise<void> }) {
  const report = result.screenshot_analysis!;
  const [selected, setSelected] = useState<string[]>(report.materials.filter((m) => m.requirement === "required").map((m) => m.id));
  const [names, setNames] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [date, setDate] = useState("");
  if (report.status !== "recognized" || !report.is_application_page) return <section className="screenshot-report gap">
    <h3>截图暂未识别为办事材料页面</h3><p>{report.message || "请重新添加包含事项名称和材料列表的截图；当前图片不作为政策或材料要求依据。"}</p>
    <p className="small muted">需要支持图片的模型。可在下方重新添加截图并选择视觉模型。</p>
  </section>;
  const picked = report.materials.filter((m) => selected.includes(m.id));
  const ready = confirmed && picked.length > 0 && picked.length <= 12 && date && picked.every((m) => (names[m.id] ?? m.name).trim());
  return <section className="screenshot-report">
    <div className="screenshot-report-heading"><span className="eyebrow">截图与政策对照</span><span className="badge">待你核对</span></div>
    <h3>{report.title || "本次办事页面"}</h3>
    <p className="small muted">截图识别可能有误，政策对照也不代表完整清单或个人资格确认。可校正名称、点选需要准备的材料；特定情形和待核实项默认不选。</p>
    {report.materials.length ? report.materials.map((m) => {
      const edited = names[m.id] !== undefined && names[m.id] !== m.name;
      const check = edited ? undefined : report.checks?.[m.id];
      return <article className="material-check" key={m.id}>
        <div className="material-check-heading"><label><input type="checkbox" checked={selected.includes(m.id)} onChange={(e) => {
          setSelected((s) => e.target.checked ? [...s, m.id] : s.filter((id) => id !== m.id)); setConfirmed(false);
        }} /><span>纳入准备清单 [{m.id}]</span></label><span className="badge">{requirements[m.requirement]}</span></div>
        <input aria-label={`材料名称 ${m.id}`} maxLength={150} value={names[m.id] ?? m.name} onChange={(e) => { setNames((n) => ({ ...n, [m.id]: e.target.value })); setConfirmed(false); }} />
        {m.condition && <p className="small">适用情形：{m.condition}</p>}
        <p className={`material-verdict ${check?.verdict === "conflict" ? "danger-action" : ""}`}>
          {edited ? "名称已校正，请重新核对政策对应关系。" : check?.verdict === "supported" ? "找到政策对应依据（仍需核对适用条件）" : check?.verdict === "conflict" ? "截图与政策存在差异，请向办理部门核实" : "当前政策尚未确认此项"}
        </p>
        <details><summary>查看截图文字与政策依据</summary><p className="small muted">用户截图 · {m.location}</p><blockquote>{m.quote}</blockquote>
          {check && <><p>{check.explanation}</p>{check.policy_quote && <blockquote>{check.policy_quote}</blockquote>}
            {check.policy_evidence_ids.map((id) => { const source = result.sources.find((s) => s.evidence_id === id); return source && <p key={id}><a href={official(source.source_url)} target="_blank" rel="noreferrer">[{id}] {source.title}</a></p>; })}</>}
        </details>
      </article>;
    }) : <p>截图中没有清晰可见的材料要求，请补充材料列表所在页面。</p>}
    {!!report.dates.length && <div className="gap"><h4>截图所示日期 · 需核实</h4>{report.dates.map((d, i) => <p key={i}>{d.label}：{d.date}<small className="muted"> · {d.quote}</small></p>)}<p className="small muted">不自动将截图日期设为官方截止日；请核对年度、批次和最新要求。</p></div>}
    {!!report.gaps.length && <div className="gap"><h4>仍需核实</h4><ul>{report.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></div>}
    <div className="screenshot-plan">
      <label>我的准备日期（不是申报截止日期）<input aria-label="材料准备日期" type="date" value={date}
        onChange={(e) => { setDate(e.target.value); setConfirmed(false); }}
        onInput={(e) => { setDate(e.currentTarget.value); setConfirmed(false); }}
        onBlur={(e) => { if (e.currentTarget.value !== date) { setDate(e.currentTarget.value); setConfirmed(false); } }} /></label>
      <label><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />我已核对识别文字、所选材料和适用情形；待核实项会作为个人准备任务记录。</label>
      <p className="small muted">已选 {picked.length} 项，最多12项；点击后直接保存到日程，不会提交政务申请。</p>
      <button className="primary" disabled={!ready || saving} onClick={async () => {
        setSaving(true); setMessage("");
        try {
        const ids = [...new Set(picked.filter((m) => names[m.id] === undefined || names[m.id] === m.name).flatMap((m) => report.checks?.[m.id]?.policy_evidence_ids || []))];
        await onSchedule({ title: report.title || result.question.slice(0, 150), date, requires_confirmation: true,
          method: report.procedure || "",
          materials: picked.map((m) => ({ text: (names[m.id] ?? m.name).trim(), evidence_id: m.id, quote: m.quote,
            source_kind: "screenshot", location: m.location, source_title: report.title || "用户截图", requirement: m.requirement, condition: m.condition })),
          sources: result.sources.filter((s) => ids.includes(s.evidence_id || "")).map((s) => ({ evidence_id: s.evidence_id!, title: s.title,
            source_url: s.source_url, version: s.version, locator: s.locator || "原文" })) });
        setMessage("已加入日程");
        } catch (error) { setMessage((error as Error).message); } finally { setSaving(false); }
      }}>{saving ? "保存中…" : "加入日程"}</button><p role="status">{message}</p>
    </div>
  </section>;
}
