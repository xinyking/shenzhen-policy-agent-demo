import { useEffect, useState } from "react";
import {
  FileText,
  Maximize2,
  Copy,
  Download,
  CalendarDays,
  ArrowUpRight,
} from "lucide-react";
import { download, official, type Result, type ScheduleDraft } from "../lib/api";
import ScreenshotReport from "./ScreenshotReport";
import FollowUp from "./FollowUp";
export default function ResultPanel({
  result,
  onExpand,
  onSchedule,
  inline = false,
  instanceId = "result",
  onRetry,
  retryDisabled = false,
  onFollowUp,
}: {
  result: Result;
  onExpand: () => void;
  onSchedule: (draft?: ScheduleDraft) => void | Promise<void>;
  inline?: boolean;
  instanceId?: string;
  onRetry?: () => void;
  retryDisabled?: boolean;
  onFollowUp?: (reply: string) => void;
}) {
  const [tab, setTab] = useState("answer"),
    [highlight, setHighlight] = useState(""),
    [copy, setCopy] = useState(false);
  const [referencesOpen, setReferencesOpen] = useState(false);
  const [plan, setPlan] = useState<ScheduleDraft | null>(null);
  const [planDate, setPlanDate] = useState("");
  const [savingPlan, setSavingPlan] = useState(false);
  const [planMessage, setPlanMessage] = useState("");
  async function savePlan(draft: ScheduleDraft) {
    setSavingPlan(true); setPlanMessage("");
    try { await onSchedule(draft); setPlan(null); setPlanMessage("已加入日程，可在日程安排中查看。"); }
    catch (error) { setPlanMessage((error as Error).message); }
    finally { setSavingPlan(false); }
  }
  function addPlan(draft?: ScheduleDraft) {
    const value = draft || result.schedule_drafts?.[0] || {
      title: (result.claims.find((claim) => claim.kind === "interpretation")?.title || result.question).slice(0, 150),
      date: null, materials: [], method: "", requires_confirmation: false, sources: [],
    };
    setPlanMessage("");
    if (value.date) { void savePlan(value); return; }
    setPlan(value); setPlanDate("");
  }

  useEffect(() => {
    setTab("answer");
    setHighlight("");
  }, [result]);
  function cite(id: string) {
    setTab("evidence");
    setHighlight(id);
    setTimeout(() => {
      const evidence = document.getElementById(`${instanceId}-evidence-${id}`);
      evidence?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      evidence?.focus({ preventScroll: true });
    }, 50);
  }
  const citedIds = [...new Set(result.claims.flatMap((claim) => claim.evidence_ids))];
  const referenceNumber = (id: string) => citedIds.indexOf(id) + 1;
  function footnote(id: string) {
    setReferencesOpen(true);
    setTimeout(() => {
      const element = document.getElementById(`${instanceId}-reference-${id}`);
      element?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      element?.focus({ preventScroll: true });
    }, 50);
  }
  const markdown = `${result.report_markdown}\n\n生成时间：${result.checked_at}\n`;
  return (
    <section className={`result-panel ${inline ? "inline-result" : ""}`}>
      {!inline && <div className="panel-heading">
        <span>
          <FileText size={17} />
          政策成果
        </span>
        <button className="icon" aria-label="放大或还原成果" onClick={onExpand}>
          <Maximize2 size={16} />
        </button>
      </div>}
      <div className="result-toolbar">
        <button
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(markdown);
              setCopy(true);
              setTimeout(() => setCopy(false), 1500);
            } catch {
              setCopy(false);
              alert("复制不可用，请下载 Markdown。");
            }
          }}
        >
          <Copy size={14} />
          {copy ? "已复制" : "复制"}
        </button>
        <button onClick={() => download("深圳政策咨询.md", markdown)}>
          <Download size={14} />
          导出
        </button>
        <button disabled={savingPlan || !!plan} onClick={() => addPlan()}>
          <CalendarDays size={14} />
          创建日程
        </button>
      </div>
      {plan && <form className="gap" onSubmit={(event) => {
        event.preventDefault(); if (planDate && !savingPlan) void savePlan({ ...plan, date: planDate });
      }}>
        <p>{plan.title}</p>
        <label>安排在哪一天？<input type="date" required aria-label="日程计划日期" value={planDate} onChange={(event) => setPlanDate(event.target.value)} /></label>
        <p className="small muted">个人准备日期；材料和备注随事项直接保存。</p>
        <button className="primary" disabled={savingPlan}>{savingPlan ? "保存中…" : "加入日程"}</button>
        <button type="button" disabled={savingPlan} onClick={() => setPlan(null)}>取消</button>
      </form>}
      {planMessage && <p role="status">{planMessage}</p>}
      <div className="result-tabs" role="tablist" aria-label="政策成果">
        <button
          role="tab"
          aria-selected={tab === "answer"}
          onClick={() => setTab("answer")}
        >
          答复
        </button>
        <button
          role="tab"
          aria-selected={tab === "evidence"}
          onClick={() => setTab("evidence")}
        >
          原文依据 <span>{result.sources.length}</span>
        </button>
      </div>
      <div className="result-content">
        {tab === "answer" ? (
          <>
            {!inline && <div className="eyebrow">POLICY BRIEF</div>}
            {!inline && <h2>
              {result.status === "answered"
                ? "给你的回复与建议"
                : result.status === "evidence_only"
                  ? "政策原文摘录"
                  : "现有资料尚不足以回答"}
            </h2>}
            <div className="result-meta">
              {result.memory_used && <span className="badge">已加载用户记忆</span>}
              {result.routing?.domains.map((domain) => <span className="badge" key={domain.id}>{domain.name}</span>)}
              <span className="badge">
                {result.execution_profile === "quick" ? "快速回答" : result.mode_used === "agent"
                  ? "深入研究"
                  : result.mode_used === "llm"
                    ? "模型问答"
                    : "数据库搜索"}
              </span>
              <small>
                {new Date(result.checked_at).toLocaleString("zh-CN", {
                  timeZone: "Asia/Hong_Kong",
                })}
              </small>
            </div>
            <p className="reading-note">
              以引用原文为依据。政策有效性及最新办理要求仍需向主管部门核对。
            </p>
            {result.screenshot_analysis && <ScreenshotReport result={result} onSchedule={onSchedule} />}
            {result.mode_used === "extractive" && (
              <div className="gap" role="status">
                <h3>{result.mode_requested === "offline" ? "政策原文搜索结果" : "本次没有生成 AI 回复"}</h3>
                <p>{(result as Result & { mode_requested?: string }).mode_requested === "offline"
                  ? "以下为本地数据库中的相关原文与出处。如需政策解读，可选择快速回答或深入研究。"
                  : result.failure ? `${({decide:"研究决策",generate:"AI回答生成",review:"引用核查"} as Record<string,string>)[result.failure.stage] || "模型处理"}未完成：${result.failure.message}。`
                  : "这条历史回答未完成模型处理，以下保留原文依据。"}</p>
                {result.failure?.stage === "review" && <p>这表示引用核查步骤未完成，不表示政策内容被判定错误；未经核查的模型草稿未展示。</p>}
                {onRetry && !result.screenshot_analysis && <button disabled={retryDisabled} onClick={onRetry}>{result.mode_requested === "offline" ? "用快速回答解读" : "重新生成 AI 答复"}</button>}
                {onRetry && !result.screenshot_analysis && retryDisabled && <p className="small muted">请等待当前任务结束，并确认已配置可用模型。</p>}
                {result.mode_requested !== "offline" && result.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
              </div>
            )}
            <div className="answer-prose">
              {result.claims.map((c, i) => (
                <section className={`answer-section ${c.kind || "interpretation"}`} key={i}>
                  {c.title && <h3>{c.kind === "interpretation"
                    ? `${result.claims.slice(0, i + 1).filter((item) => item.kind === "interpretation").length}. ${c.title}`
                    : c.title}</h3>}
                  <p>{c.text}<sup className="inline-references">
                    {c.evidence_ids.map((id) => (
                      <button key={id} onClick={() => footnote(id)} aria-label={`查看引用 ${referenceNumber(id)}`}>
                        [{referenceNumber(id)}]
                      </button>
                    ))}
                  </sup></p>
                </section>
              ))}
            </div>
            {!result.claims.length && <p>{result.answer}</p>}
            {result.schedule_drafts?.length ? <section className="gap">
              <h3>申请与材料规划草案</h3>
              <p className="small muted">选择计划日期即可加入日程。</p>
              {result.schedule_drafts.map((draft,index) => <article key={index}>
                <h4>{draft.title}</h4>
                {draft.materials.length ? <ul>{draft.materials.map((m,i) => <li key={i}>{m.text}<details><summary>材料依据 [{m.evidence_id}]</summary><blockquote>{m.quote}</blockquote></details></li>)}</ul> : <p>当前证据未提供完整材料清单，请核查官方要求。</p>}
                <button className="text-button" disabled={savingPlan || !!plan} onClick={() => addPlan(draft)}>加入日程</button>
              </article>)}
            </section> : null}
            {result.multi_agent_trace && <details><summary>查看专业Agent协作记录</summary><pre>{JSON.stringify(result.multi_agent_trace,null,2)}</pre></details>}
            {result.follow_up && onFollowUp && <FollowUp value={result.follow_up} disabled={retryDisabled} onSend={onFollowUp} />}
            {citedIds.length > 0 && (
              <details className="answer-references" open={referencesOpen} onToggle={(event) => setReferencesOpen(event.currentTarget.open)} aria-label="引用片段">
                <summary>引用片段 · {citedIds.length}条</summary>
                <p className="small muted">编号对应正文上标；以下为检索到的原文窗口，完整来源可在原文依据页查看。</p>
                {citedIds.map((id) => {
                  const source = result.sources.find((item) => item.evidence_id === id);
                  return source ? (
                    <article key={id} id={`${instanceId}-reference-${id}`} tabIndex={-1} className="reference-item">
                      <h4>[{referenceNumber(id)}] {source.title}</h4>
                      <p className="small muted">{source.locator} · 发布：{source.published_at || "未提取"}</p>
                      <blockquote>{source.text}</blockquote>
                      <a href={official(source.source_url)} target="_blank" rel="noreferrer">官方原文 <ArrowUpRight size={12} /></a>
                      <button className="text-button" onClick={() => cite(id)}>查看完整依据</button>
                    </article>
                  ) : null;
                })}
              </details>
            )}
            {result.agent_trace && (
              <details>
                <summary>查看实际研究步骤</summary>
                <pre>{JSON.stringify(result.agent_trace, null, 2)}</pre>
              </details>
            )}
          </>
        ) : (
          <>
            <div className="eyebrow">TRACEABLE EVIDENCE</div>
            <h2>回到政策原文</h2>
            <p className="muted">
              这里展示本次使用的快照证据，分别保留每份政策的日期和来源。
            </p>
            {result.sources.map((s) => (
              <article
                id={`${instanceId}-evidence-${s.evidence_id}`}
                tabIndex={-1}
                className={`evidence ${highlight === s.evidence_id ? "highlight" : ""}`}
                key={s.evidence_id}
              >
                <span className="badge">{s.evidence_id}</span>
                <h3>{s.title}</h3>
                <dl>
                  <dt>发布日期</dt>
                  <dd>{s.published_at || "未提取"}</dd>
                  <dt>生效 / 申报日期</dt>
                  <dd>当前接口未提供结构化信息</dd>
                  <dt>政策有效性</dt>
                  <dd>待确认</dd>
                  <dt>原文位置</dt>
                  <dd>{s.locator}</dd>
                  <dt>最近检查</dt>
                  <dd>{s.last_checked_at}</dd>
                </dl>
                <blockquote>{s.text}</blockquote>
                <a
                  href={official(s.source_url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开官方原文 <ArrowUpRight size={14} />
                </a>
                <small className="version">快照版本 {s.version}</small>
              </article>
            ))}
          </>
        )}
      </div>
    </section>
  );
}
