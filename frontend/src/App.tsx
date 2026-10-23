import { useDeepAgentSession, waitForRun } from "./hooks/useDeepAgentSession";
import { useEffect, useState, useRef } from "react";
import {
  Plus,
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  Settings,
  MessageSquare,
  PanelLeftClose,
  Menu,
  Leaf,
  FileText,
  Download,
  X,
  Search,
  Trash2,
  Zap,
  Telescope,
} from "lucide-react";
import {
  api,
  uploadScreenshot,
  download,
  official,
  type Result,
  type Source,
  type ModelStatus,
  type ScheduleDraft,
  type RunEvent,
  type RunStatus,
} from "./lib/api";
import { load, save, empty, type LocalData, type Schedule } from "./lib/storage";

import ResultPanel from "./components/ResultPanel";
import ScheduleWorkspace from "./components/ScheduleWorkspace";
import HistoryItem from "./components/HistoryItem";
import QuickContext, { backgroundText } from "./components/QuickContext";
import RunProgress from "./components/RunProgress";
import ScreenshotAttachment, { type ScreenshotSelection } from "./components/ScreenshotAttachment";

const examples = [
  "刚毕业来深圳，有哪些就业补贴可以了解？",
  "职业技能培训补贴有哪些政策依据？",
  "2026 年就业政策有哪些需要核对的变化？",
];
export default function App() {
  const [data, setData] = useState<LocalData>(empty),
    [ready, setReady] = useState(false),
    [page, setPage] = useState("chat"),
    [id, setId] = useState(""),
    [question, setQuestion] = useState(""),
    [context, setContext] = useState(""),
    [showContext, setShowContext] = useState(false),
    [mode, setMode] = useState<"quick" | "multi_agent" | "offline">("quick"),
    [pending, setPending] = useState<string | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [collapsed, setCollapsed] = useState(false),
    [drawer, setDrawer] = useState(false),
    [historySearch, setHistorySearch] = useState(""),
    [allHistory, setAllHistory] = useState(false);
  const [sources, setSources] = useState<Source[]>([]),
    [query, setQuery] = useState(""),
    [hits, setHits] = useState<Source[] | null>(null),
    [detail, setDetail] = useState<Source | null>(null),
    [model, setModel] = useState<ModelStatus | null>(null),
    [base, setBase] = useState(""),
    [name, setName] = useState(""),
    [key, setKey] = useState(""),
    [configBusy, setConfigBusy] = useState(false),
    [schedule, setSchedule] = useState<Schedule | null>(null);
  const [background, setBackground] = useState<Record<string, string>>({});
  const [useMemory, setUseMemory] = useState(false);
  const [memoryProfile, setMemoryProfile] = useState("");
  const [memoryStatus, setMemoryStatus] = useState("");
  const [pendingTurn, setPendingTurn] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [screenshot, setScreenshot] = useState<ScreenshotSelection | null>(null);
  const [visionName, setVisionName] = useState("");
  const subscription = useDeepAgentSession();
  const restored = useRef(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (pending && pending === id && messagesRef.current)
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
  }, [pending, id]);
  const draft = useRef(question);
  draft.current = question;
  const activeId = useRef(id);
  activeId.current = id;
  const consultation = data.consultations.find((c) => c.id === id);
  useEffect(() => {
    load()
      .then((d) => {
        setData(d);
        setReady(true);
      })
      .catch(() => {
        setError("浏览器存储不可用，本次内容仅保存在内存中。");
        setReady(true);
      });
    api<{ sources: Source[] }>("/v1/sources")
      .then((v) => setSources(v.sources))
      .catch((e) => setError(e.message));
    api<ModelStatus>("/v1/model")
      .then((v) => {
        setModel(v);
        setBase(v.base_url);
        setName(v.model_name);
        setVisionName(v.vision_model_name || "");
        if (v.load_error) setError("本地模型配置文件无法读取，请在设置中重新保存配置。");
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!ready) return;
    api<{profile:string; applications:Schedule[]}>("/v1/memory").then((v) => {
      setMemoryProfile(v.profile);
      setMemoryStatus("本地长期记忆可用");
      setData((d) => ({ ...d, schedules: [...d.schedules, ...v.applications.filter((s) => !d.schedules.some((local) => local.id === s.id))] }));
    }).catch(() => setMemoryStatus("当前后端未提供记忆接口，请重启新版后端。"));
  }, [ready]);
  useEffect(() => {
    if (ready)
      save(data).catch(() => setError("本地保存失败，请导出需要保留的记录。"));
  }, [data, ready]);
  function newChat() {
    setScreenshot(null);
    setNotice("");
    activeId.current = "";
    setId("");
    setQuestion("");
    setContext("");
    setBackground({});
    setPage("chat");
    setDrawer(false);
    setError("");
  }
  function openChat(cid: string) {
    setScreenshot(null);
    setNotice("");
    activeId.current = cid;
    setId(cid);
    setPage("chat");
    setDrawer(false);
    setContext("");
    setBackground({});
    setQuestion("");
  }
  async function ask(retry?: { question: string; context: string }) {
    const image = retry ? null : screenshot;
    const q = (retry?.question || question || (image ? "请核对截图中这个事项的材料、办理方法，并整理待确认的日程清单。" : "")).trim();
    if (pending || !q || !ready || (mode !== "offline" && !model)) return;
    if ((retry || mode !== "offline") && !model?.configured) {
      setNotice("请先在设置中配置模型 API，或选择数据库搜索查看政策原文。");
      setPage("settings"); return;
    }
    if (image && (!image.consent || !model?.configured || mode === "offline")) {
      setError("请先配置支持图片的模型、选择AI问答，并勾选截图发送确认。"); return;
    }
    const cid = id || crypto.randomUUID(), turnId = crypto.randomUUID();
    const recent = consultation?.turns.slice(-2).map((turn) => `用户：${turn.question}${turn.result ? "\n历史答复主题（不是证据）：" + turn.result.claims.slice(0, 3).map((claim) => `${claim.title || ""}：${claim.text.slice(0, 60)}`).join("；") : ""}`).join("\n").slice(-600) || "";
    const sentContext = retry ? retry.context : [backgroundText(background), showContext ? context : "",
      recent ? `本咨询最近两轮用户消息（仅理解指代，不是政策证据；当前消息优先）：\n${recent}` : ""].filter(Boolean).join("\n").slice(0, 1000);
    setPending(cid); setPendingTurn(turnId); setNotice(""); setError(""); setQuestion("");
    if (!id) { activeId.current = cid; setId(cid); }
    setData((d) => ({ ...d, consultations: id
      ? d.consultations.map((c) => c.id === cid ? { ...c, turns: [...c.turns, { id: turnId, question: q, context: sentContext, screenshotName: image?.file.name }] } : c)
      : [{ id: cid, title: q.slice(0,28), created: new Date().toISOString(), turns: [{ id: turnId, question: q, context: sentContext, screenshotName: image?.file.name }] }, ...d.consultations] }));
    try {
      const screenshotId = image ? await uploadScreenshot(image.file) : undefined;
      const run = await api<RunStatus>("/v1/runs", "POST", {
        question: q,
        context: sentContext,
        mode: retry ? "quick" : mode,
        top_k: 5,
        use_memory: useMemory,
        session_id: cid,
        client_request_id: turnId,
        ...(image ? { screenshot_id: screenshotId, image_consent: true, vision_model: image.visionModel.trim() } : {}),
      });
      if (image) setScreenshot(null);
      setData((d) => ({ ...d, consultations: d.consultations.map((c) => c.id === cid ? { ...c, turns: c.turns.map((t) => t.id === turnId ? { ...t, runId: run.run_id } : t) } : c) }));
      await monitorRun(run.run_id, cid, turnId);
    } catch (e) {
      recordRunError(cid, turnId, (e as Error).message);
      setPending(null); setPendingTurn(null);
    }
  }
  function recordRunError(cid: string, turnId: string, message: string) {
    if (message.includes("任务已取消")) setNotice(message); else setError(message);
    setData((d) => ({ ...d, consultations: d.consultations.map((c) => c.id === cid ? { ...c, turns: c.turns.map((turn) => turn.id === turnId ? { ...turn, error: message } : turn) } : c) }));
  }
  async function monitorRun(runId: string, cid: string, turnId: string) {
    setActiveRun(runId); setCancelling(false);
    const controller = new AbortController();
    subscription.current = controller;
    try {
      const r = await waitForRun(runId, (event: RunEvent) => {
        setData((d) => ({ ...d, consultations: d.consultations.map((c) => c.id === cid ? { ...c, turns: c.turns.map((t) => t.id === turnId
          ? { ...t, runEvents: [...(t.runEvents || []).filter((e) => e.seq !== event.seq), event].sort((a,b) => a.seq - b.seq).slice(-100) } : t) } : c) }));
      }, controller.signal);
      setData((d) => ({
        ...d,
        consultations: d.consultations.map((c) =>
          c.id === cid
            ? { ...c, turns: c.turns.map((turn) => turn.id === turnId ? { ...turn, result: r } : turn) }
            : c,
        ),
      }));
      setNotice(r.mode_requested === "offline" ? "数据库搜索完成，可查看政策原文与出处。" : r.mode_used === "extractive"
        ? "本次仅返回原文，没有生成 AI 回复。请查看成果中的原因说明。"
        : "答复完成，可查看政策信息与引用依据。");
    } catch (e) {
      recordRunError(cid, turnId, (e as Error).message);
    } finally {
      setPending(null);
      setPendingTurn(null);
      setActiveRun(null); setCancelling(false);
    }
  }
  useEffect(() => {
    if (!ready || restored.current) return;
    restored.current = true;
    const c = data.consultations.find((c) => c.turns.some((t) => t.runId && !t.result && !t.error));
    const turn = c?.turns.find((t) => t.runId && !t.result && !t.error);
    if (c && turn?.id && turn.runId) {
      setPending(c.id); setPendingTurn(turn.id);
      void monitorRun(turn.runId, c.id, turn.id);
    }
  }, [ready]);
  async function cancelRun() {
    if (!activeRun || cancelling) return;
    setCancelling(true);
    try { await api(`/v1/runs/${activeRun}/cancel`, "POST"); }
    catch (e) { setCancelling(false); setError((e as Error).message); }
  }
  async function configure(action: string) {
    setConfigBusy(true);
    setError("");
    setNotice("");
    try {
      if (action === "test") {
        const v = await api<{ message: string }>("/v1/model/test", "POST");
        setNotice(v.message);
      } else {
        const v = await api<ModelStatus>(
          "/v1/model",
          action === "clear" ? "DELETE" : "PUT",
          action === "clear"
            ? undefined
            : { base_url: base, model_name: name, api_key: key, vision_model_name: visionName },
        );
        setModel(v);
        setBase(v.base_url);
        setName(v.model_name);
        setVisionName(v.vision_model_name || "");
        setKey("");
        setNotice(
          action === "clear"
            ? "已清除运行时和本地文件中的模型配置。"
            : v.persistence === "local_private_file"
              ? "配置已保存到本地私有文件，重启后自动读取。"
              : "当前运行的是旧后端，配置仅在内存。请重启新版后端后再保存一次。",
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setKey("");
      setConfigBusy(false);
    }
  }
  async function persistSchedule(value: Schedule) {
    try {
      await api("/v1/memory/applications", "POST", value);
      setData((d) => ({ ...d, schedules: [...d.schedules.filter((s) => s.id !== value.id), value] }));
      return true;
    } catch (e) { setError("申请日程未保存：" + (e as Error).message); return false; }
  }
  async function deleteSchedule(sid: string) {
    try { await api(`/v1/memory/applications/${encodeURIComponent(sid)}`, "DELETE");
      setData((d) => ({ ...d, schedules: d.schedules.filter((s) => s.id !== sid) }));
    } catch (e) { setError((e as Error).message); }
  }
  async function createSchedule(draft?: ScheduleDraft) {
    if (!draft?.date) throw new Error("请先选择计划日期");
    const saved = await persistSchedule({
      id: crypto.randomUUID(), title: draft.title, date: draft.date,
      notes: [draft.notes || draft.method, "日期为个人准备计划，非政策截止日。",
        !draft.materials.length ? "现有证据未明确完整材料清单，请核查官方指南。" : ""].filter(Boolean).join("\n").slice(0, 1000),
      applicationStatus: "planned",
      materials: draft.materials.map((m) => m.text).join("\n"),
      materialsChecklist: draft.materials.map((m) => ({ ...m, done: false })),
      applicationMethod: "", policySources: draft.sources, done: false,
      consultationId: id || undefined, created: new Date().toISOString(),
    });
    if (!saved) throw new Error("日程未保存，请稍后重试");
  }
  return (
    <div
      className={`app ${collapsed ? "collapsed" : ""} `}
    >
      <aside className={`sidebar ${drawer ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-icon">
            <Leaf size={22} />
          </span>
          <span>
            深圳政策助手<small>SHENZHEN · POLICY</small>
          </span>
          <button
            className="icon collapse"
            aria-label="折叠导航"
            onClick={() => setCollapsed(!collapsed)}
          >
            <PanelLeftClose size={17} />
          </button>
        </div>
        <button className="new-chat" onClick={newChat}>
          <Plus size={18} />
          <span>新建咨询</span>
        </button>
        <nav>
          <button
            className={page === "chat" ? "active" : ""}
            onClick={() => {
              setPage("chat");
              setDrawer(false);
            }}
          >
            <MessageSquare size={18} />
            <span>政策咨询</span>
          </button>
          <button
            className={page === "sources" ? "active" : ""}
            onClick={() => {
              setPage("sources");
              setDrawer(false);
            }}
          >
            <BookOpen size={18} />
            <span>政策数据库</span>
            <small>{sources.length}</small>
          </button>
          <button
            className={page === "schedule" ? "active" : ""}
            onClick={() => {
              setPage("schedule");
              setDrawer(false);
            }}
          >
            <CalendarDays size={18} />
            <span>日程安排</span>
          </button>
        </nav>
        <div className="history">
          <div className="eyebrow">
            最近咨询{" "}
            <button onClick={() => setAllHistory(!allHistory)}>
              {allHistory ? "收起" : "全部"}
            </button>
          </div>
          {allHistory && (
            <input
              aria-label="搜索咨询"
              placeholder="搜索咨询记录"
              value={historySearch}
              onChange={(e) => setHistorySearch(e.target.value)}
            />
          )}
          <div className="history-list">
            {data.consultations
              .filter((c) => c.title.includes(historySearch))
              .slice(0, allHistory ? undefined : 8)
              .map((c) => (
                <HistoryItem key={c.id} consultation={c} current={id === c.id} busy={pending === c.id}
                  onOpen={() => openChat(c.id)}
                  onRename={(title) => setData((d) => ({ ...d, consultations: d.consultations.map((item) => item.id === c.id ? { ...item, title } : item) }))}
                  onDelete={() => {
                    if (pending === c.id) return;
                    setData((d) => ({ ...d, consultations: d.consultations.filter((item) => item.id !== c.id) }));
                    if (id === c.id) newChat();
                  }} />
              ))}
            {!data.consultations.length && (
              <p className="muted small">你的咨询将保存在这里</p>
            )}
          </div>
        </div>
        <div className="sidebar-bottom">
          <p>
            <span className="dot" />
            本地工作空间
          </p>
          <button
            onClick={() => {
              setPage("settings");
              setDrawer(false);
            }}
          >
            <span className="avatar">我</span>
            <span>
              个人资料与设置<small>本机个人工作空间</small>
            </span>
            <Settings size={17} />
          </button>
        </div>
      </aside>
      {drawer && (
        <button
          className="scrim"
          aria-label="关闭导航"
          onClick={() => setDrawer(false)}
        />
      )}
      <main>
        <header className="topbar">
          <button
            className="icon mobile-menu"
            aria-label="打开导航"
            onClick={() => {
              setCollapsed(false);
              setDrawer(true);
            }}
          >
            <Menu size={20} />
          </button>
          <button
            className="icon desktop-restore"
            aria-label="展开导航"
            onClick={() => setCollapsed(false)}
          >
            <Menu size={18} />
          </button>
          <span>
            个人发展 /{" "}
            <b>
              {
                (
                  {
                    chat: "政策咨询",
                    sources: "政策数据库",
                    schedule: "日程安排",
                    settings: "资料与模型设置",
                  } as Record<string, string>
                )[page]
              }
            </b>
          </span>
          <span className="top-status">
            <span className="dot" />
            深圳市 · 本地政策快照
          </span>
        </header>
        {(error || notice) && (
          <div
            className={`banner ${error ? "error" : ""}`}
            role={error ? "alert" : "status"}
          >
            {error || notice}
            <button
              className="icon"
              aria-label="关闭提示"
              onClick={() => {
                setError("");
                setNotice("");
              }}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {page === "chat" ? (
          <>
            <div className="workspace conversation-workspace">
              <section className="chat-panel">
                <div className="panel-heading">
                  <span>{consultation?.title || "新的政策咨询"}</span>
                  <small>{pending === id ? "查询中" : "引用可追溯"}</small>
                </div>
                <div className="messages" ref={messagesRef}>
                  {!consultation?.turns.length ? (
                    <div className="welcome">
                      <div className="welcome-mark">
                        <Leaf size={30} />
                      </div>
                      <div className="eyebrow">让政策信息，更贴近你</div>
                      <h1>
                        你的下一步，
                        <br />
                        从了解政策开始。
                      </h1>
                      <p>
                        聊聊就业、补贴与个人发展。
                        <br />
                        我们从深圳官方政策中寻找依据。
                      </p>
                      <div className="examples">
                        {examples.map((q, i) => (
                          <button key={q} onClick={() => setQuestion(q)}>
                            <span className="example-index">0{i + 1}</span>
                            <span>{q}</span>
                            <ArrowUpRight size={17} />
                          </button>
                        ))}
                      </div>
                      <div className="welcome-note">
                        <BookOpen size={14} />
                        当前收录 {sources.length} 份来源，非完整政策库
                      </div>
                    </div>
                  ) : (
                    consultation.turns.map((turn, i) => (
                      <div className="turn" key={turn.id || i}>
                        <div className="user-message">{turn.question}</div>
                        {turn.screenshotName && <p className="turn-background">已附办事截图：{turn.screenshotName} · 原图不保存在咨询数据库</p>}
                        {turn.context && <p className="turn-background">本条背景：{turn.context}</p>}
                        <div className="turn-actions">
                          <button className="text-button" disabled={!!pending} onClick={() => {
                            if (!confirm("删除这条问题及其回答？其他聊天记录会保留。")) return;
                            setData((current) => ({ ...current, consultations: current.consultations.map((item) => item.id === id
                              ? { ...item, turns: item.turns.filter((_, index) => index !== i) } : item) }));
                                                  }}><Trash2 size={14} />删除本条问答</button>
                        </div>
                        {(turn.runEvents?.length || pendingTurn === turn.id) ? <RunProgress events={turn.runEvents || []} busy={pendingTurn === turn.id}
                          onCancel={pendingTurn === turn.id && activeRun ? cancelRun : undefined} cancelling={cancelling} /> : null}
                        {turn.result ? <ResultPanel result={turn.result} inline instanceId={`turn-${consultation.id}-${turn.id || i}`}
                          onExpand={() => {}} onSchedule={createSchedule}
                          onFollowUp={(reply) => void ask({ question: reply, context: `原始政策问题：${turn.question}\n${turn.context || ""}\n本轮仅结合用户补充情况继续解释，不认定资格。`.slice(0, 1000) })}
                          onRetry={() => void ask({ question: turn.question, context: turn.context || "" })} retryDisabled={!!pending || !model?.configured} /> : <div className="pending" role="status">
                          {turn.error ? turn.error.includes("任务已取消") ? turn.error : `请求失败：${turn.error}` : pendingTurn === turn.id ? "问题已发送，正在等待后端答复…" : "上次请求未完成，可重新发送。"}
                          {turn.runId && turn.error && pendingTurn !== turn.id && <button className="text-button" disabled={!!pending} onClick={() => {
                            setPending(consultation.id); setPendingTurn(turn.id!); setError("");
                            setData((d) => ({ ...d, consultations: d.consultations.map((c) => c.id === consultation.id ? { ...c, turns: c.turns.map((t) => t.id === turn.id ? { ...t, error: undefined } : t) } : c) }));
                            void monitorRun(turn.runId!, consultation.id, turn.id!);
                          }}>重新连接任务</button>}
                          {pendingTurn !== turn.id && <button className="text-button" onClick={() => { setQuestion(turn.question); setContext(turn.context || ""); setShowContext(Boolean(turn.context)); }}>重新编辑并发送</button>}
                        </div>}
                        {turn.result && turn.result.missing_information.length > 0 && <button className="text-button" onClick={() => {
                          setQuestion(turn.question);
                          setBackground({});
                          setContext(turn.context || "");
                          setShowContext(Boolean(turn.context));
                          document.getElementById("background-options")?.scrollIntoView({ block: "nearest" });
                        }}>调整问题或查询范围，再次询问</button>}

                      </div>
                    ))
                  )}

                </div>
                <div className="composer">
                  <div className="composer-options">
                  <details className="optional-query-settings"><summary>可选查询设置{Object.keys(background).length || showContext || useMemory ? " · 已启用背景或记忆" : " · 无需填写身份信息"}</summary>
                  <div id="background-options"><QuickContext values={background} onChange={setBackground} question={question} /></div>
                  <label className="small"><input type="checkbox" checked={useMemory} onChange={(e) => setUseMemory(e.target.checked)} />使用长期记忆与本咨询上下文（本机保存，随问题发送模型；历史答复非证据）</label>
                  {backgroundText(background) && <p className="small muted">下条发送：{backgroundText(background)}</p>}
                  <button
                    className="context-toggle"
                    aria-expanded={showContext}
                    onClick={() => setShowContext(!showContext)}
                  >
                    <Plus size={14} />
                    本次背景 {showContext ? "· 已启用" : "· 未发送"}
                  </button>
                  {showContext && (
                    <div className="context-editor">
                      <textarea
                        aria-label="本次背景"
                        placeholder="例如：毕业年份、所在区、就业情况。仅填写本次必要的信息。"
                        maxLength={1000}
                        value={context}
                        onChange={(e) => setContext(e.target.value)}
                      />
                      <div>
                        <small>
                          {context.length}/1000 · 随问题发送给所选模型
                        </small>
                        <button
                          className="text-button"
                          onClick={() =>
                            setContext(data.profile.slice(0, 1000))
                          }
                        >
                          使用已保存资料
                        </button>
                      </div>
                    </div>
                  )}
                  </details>
                  <ScreenshotAttachment value={screenshot} onChange={setScreenshot} busy={!!pending} modelName={model?.vision_model_name || model?.model_name || ""} />
                  </div>
                  <div className="input-box">
                    <textarea
                      aria-label="政策问题"
                      placeholder="例如：毕业生补贴面向哪些人？申请流程和材料是什么？"
                      maxLength={500}
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={(e) => {
                        if (
                          e.key === "Enter" &&
                          !e.shiftKey &&
                          !e.nativeEvent.isComposing
                        ) {
                          e.preventDefault();
                          ask();
                        }
                      }}
                    />
                    <div className="composer-tools">
                      <div className="answer-modes" role="group" aria-label="问答模式">
                        <button type="button" aria-pressed={mode === "quick"} disabled={!!pending} onClick={() => setMode("quick")}><Zap size={14} />快速回答</button>
                        <button type="button" aria-pressed={mode === "multi_agent"} disabled={!!pending} onClick={() => setMode("multi_agent")}><Telescope size={14} />深入研究</button>
                        <button type="button" aria-pressed={mode === "offline"} disabled={!!pending} onClick={() => setMode("offline")}><Search size={14} />数据库搜索</button>
                      </div>
                      <span>{question.length}/500</span>
                      <button
                        className="send"
                        aria-label="发送问题"
                        disabled={!!pending || (!question.trim() && !screenshot) || !ready || (mode !== "offline" && !model) || (!!screenshot && (!screenshot.consent || !model?.configured || mode === "offline"))}
                        onClick={() => void ask()}
                      >
                        <ArrowUp size={19} />
                      </button>
                    </div>
                  </div>
                  <p className="composer-note">
                    {mode === "offline" ? "搜索本地政策数据库，展示原文与出处，不调用模型。"
                      : mode === "multi_agent" ? "多轮检索与补查，适合复杂问题；用时较长，回答附引用。"
                      : "基于政策原文整理回答，并核对引用。"}
                    {mode !== "offline" && !model?.configured && " 需先在设置中配置模型 API。"}
                    <span className="composer-privacy">本咨询最近两轮用于追问 · 长期记忆可选</span>
                  </p>
                </div>
              </section>

            </div>
          </>
        ) : page === "sources" ? (
          <section key="sources" className="page">
            <div className="page-title">
              <div className="eyebrow">OFFICIAL SOURCES</div>
              <h1>政策数据库</h1>
              <p>
                当前收录 {sources.length}{" "}
                份官方来源快照。检索结果不代表全部深圳政策。
              </p>
            </div>
            <form
              className="search-bar"
              onSubmit={async (e) => {
                e.preventDefault();
                setError("");
                try {
                  const v = await api<{ hits: Source[] }>(
                    "/v1/search",
                    "POST",
                    { query, top_k: 20 },
                  );
                  setHits(v.hits);
                } catch (err) {
                  setError((err as Error).message);
                }
              }}
            >
              <Search size={18} />
              <input
                aria-label="政策关键词"
                placeholder="搜索就业、补贴、毕业生…"
                value={query}
                maxLength={500}
                onChange={(e) => setQuery(e.target.value)}
              />
              <button className="primary" disabled={!query.trim()}>
                搜索
              </button>
              {hits && (
                <button type="button" onClick={() => setHits(null)}>
                  重置
                </button>
              )}
            </form>
            {detail ? (
              <article className="source-detail">
                <button className="text-button" onClick={() => setDetail(null)}>
                  ← 返回来源列表
                </button>
                <h2>{detail.title}</h2>
                <p>
                  发布日期：{detail.published_at || "未提取"} · 有效性：待确认
                </p>
                <a
                  href={official(detail.source_url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开官方原文 ↗
                </a>
                <p className="source-text">{detail.text}</p>
                <button
                  className="primary"
                  onClick={() => {
                    newChat();
                    setQuestion(`关于《${detail.title}》，`);
                  }}
                >
                  基于这份政策咨询
                </button>
              </article>
            ) : (
              <div className="source-list">
                {(hits || sources).map((s, i) => (
                  <button
                    className="source-card"
                    key={s.source_id + i}
                    onClick={async () => {
                      try {
                        setDetail(
                          await api<Source>(
                            `/v1/sources/${encodeURIComponent(s.source_id)}`,
                          ),
                        );
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    <div className="source-card-icon">
                      <FileText size={20} />
                    </div>
                    <div>
                      <small>
                        {s.last_check_status === "success"
                          ? "快照检查成功"
                          : "来源需核查"}{" "}
                        · 政策有效性待确认
                      </small>
                      <h3>{s.title}</h3>
                      {s.text && <p>{s.text}</p>}
                      <span>
                        发布：{s.published_at || "未提取"} · 检查：
                        {s.last_checked_at?.slice(0, 10)}
                      </span>
                    </div>
                    <ArrowUpRight size={18} />
                  </button>
                ))}
                {hits?.length === 0 && (
                  <p>未找到匹配段落，请更换关键词；这不代表没有相关政策。</p>
                )}
              </div>
            )}
          </section>
        ) : page === "settings" ? (
          <section key="settings" className="page settings-page">
            <div className="page-title">
              <div className="eyebrow">YOUR WORKSPACE</div>
              <h1>资料与模型设置</h1>
              <p>选择本次咨询所需的信息，连接你自己的模型服务。</p>
            </div>
            <div className="settings-grid">
              <section className="card">
                <h2>模型 API</h2>
                <span className={`badge ${model?.configured ? "good" : ""}`}>
                  {model?.configured ? "已配置" : "尚未配置"}
                </span>
                <p>
                  兼容 Chat Completions 的接口，需支持 JSON
                  输出。填写接口根路径，例如以 /v1 结尾；系统自动追加
                  /chat/completions。
                </p>
                <label>
                  API Base URL
                  <input
                    value={base}
                    onChange={(e) => setBase(e.target.value)}
                    placeholder="https://你的服务商/v1"
                    autoComplete="off"
                  />
                </label>
                <label>
                  模型名称
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="填写服务商提供的模型 ID"
                    autoComplete="off"
                  />
                </label>
                <label>截图视觉模型（可选）<input value={visionName} maxLength={200} onChange={(e) => setVisionName(e.target.value)} placeholder="同一API服务中支持图片的模型ID；留空使用当前模型" autoComplete="off" /></label>
                <label>
                  API Key
                  <input
                    type="password"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder={
                      model?.key_present
                        ? "已保存；留空保留当前 Key"
                        : "输入 API Key"
                    }
                    autoComplete="new-password"
                  />
                </label>
                <p className="small muted">
                  Key
                  {model?.persistence === "local_private_file"
                    ? "保存到本机 data/private/model-config.json，重启自动读取；该文件含明文 Key，已被 Git 忽略。不会写入聊天记录、导出或浏览器存储。完整服务器环境变量配置优先。"
                    : "当前运行的后端仅支持内存配置。重启新版后端后保存一次，将写入本地私有配置文件，之后重启无需重复输入。"}
                </p>
                <div className="actions">
                  <button
                    className="primary"
                    disabled={configBusy || !base.trim() || !name.trim()}
                    onClick={() => configure("save")}
                  >
                    保存配置
                  </button>
                  <button
                    disabled={configBusy || !model?.configured}
                    onClick={() => configure("test")}
                  >
                    {configBusy ? "处理中…" : "测试连接"}
                  </button>
                  <button
                    disabled={configBusy}
                    onClick={() => configure("clear")}
                  >
                    清除
                  </button>
                </div>
                <small>测试连接会实际调用一次模型 API，可能产生费用。</small>
              </section>
              <section className="card">
                <h2>可选个人背景</h2>
                <p className="small muted">{memoryStatus}。已保存记忆：{memoryProfile || "暂无"}</p>
                <div className="actions">
                  <button onClick={async () => { try { const v=await api<{profile:string}>("/v1/memory","PUT",{profile:data.profile}); setMemoryProfile(v.profile); setNotice("长期记忆已保存；开启记忆后随问题使用。"); } catch(e) { setError((e as Error).message); } }}>保存为长期记忆</button>
                  <button onClick={async () => { if (!confirm("清空后端个人记忆、会话上下文及申请记录？浏览器日程副本仍保留。")) return; try { await api("/v1/memory","DELETE"); setMemoryProfile(""); setNotice("后端个人记忆已清空。"); } catch(e) { setError((e as Error).message); } }}>清空后端记忆</button>
                </div>
                <p>
                  输入默认保存在浏览器；点击“保存为长期记忆”后同步本地后端。咨询时启用记忆或确认使用资料才发送给模型。
                </p>
                <textarea
                  aria-label="个人背景资料"
                  maxLength={1000}
                  placeholder="所在区、毕业年份、就业情况或其他必要背景。请勿填写身份证号、银行信息。"
                  value={data.profile}
                  onChange={(e) =>
                    setData((d) => ({ ...d, profile: e.target.value }))
                  }
                />
                <button onClick={() => setData((d) => ({ ...d, profile: "" }))}>
                  清除资料
                </button>
                <hr />
                <h2>本地数据</h2>
                <p>
                  {data.consultations.length} 条咨询 · {data.schedules.length}{" "}
                  条日程
                  <br />
                  仅保存在此浏览器，无跨设备同步。
                </p>
                <div className="actions">
                  <button
                    onClick={() =>
                      download(
                        "深圳政策助手-本地记录.json",
                        JSON.stringify(
                          {
                            schema_version: 1,
                            exported_at: new Date().toISOString(),
                            ...data,
                          },
                          null,
                          2,
                        ),
                      )
                    }
                  >
                    <Download size={15} />
                    导出记录
                  </button>
                  <button
                    onClick={() => {
                      if (confirm("清除本浏览器的全部咨询、日程和个人资料？")) {
                        setData({ ...empty });
                        newChat();
                      }
                    }}
                  >
                    <Trash2 size={15} />
                    清除全部
                  </button>
                </div>
              </section>
            </div>
          </section>
        ) : (
          <ScheduleWorkspace
            schedules={data.schedules}
            consultations={data.consultations}
            draft={schedule}
            setDraft={setSchedule}
            onSave={persistSchedule}
            onToggle={(sid) => { const value = data.schedules.find((s) => s.id === sid); if (value) persistSchedule({ ...value, done: !value.done }); }}
            onDelete={deleteSchedule}
            onChecklistChange={(sid,index) => { const value = data.schedules.find((s) => s.id === sid); if (value) persistSchedule({ ...value, materialsChecklist: value.materialsChecklist?.map((m,i) => i === index ? { ...m, done: !m.done } : m) }); }}
            onConsultation={openChat}
          />
        )}
      </main>
    </div>
  );
}
