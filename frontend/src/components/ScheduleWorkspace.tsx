import { useState } from "react";
import {
  Plus,
  ChevronLeft,
  ChevronRight,
  Check,
  Trash2,
  CalendarDays,
  FileText,
} from "lucide-react";
import type { Consultation, Schedule } from "../lib/storage";
import { official } from "../lib/api";

export function hongKongToday() {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Hong_Kong",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${value("year")}-${value("month")}-${value("day")}`;
}
const dateKey = (date: Date) => date.toISOString().slice(0, 10);
export function monthDays(month: string) {
  const [year, number] = month.split("-").map(Number);
  const start = new Date(Date.UTC(year, number - 1, 1));
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setUTCDate(date.getUTCDate() + index);
    return {
      key: dateKey(date),
      day: date.getUTCDate(),
      current: dateKey(date).startsWith(month),
    };
  });
}
type Props = {
  schedules: Schedule[];
  consultations: Consultation[];
  draft: Schedule | null;
  setDraft: (value: Schedule | null) => void;
  onSave: (value: Schedule) => Promise<boolean>;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onConsultation: (id: string) => void;
  onChecklistChange: (id: string, index: number) => void;
};
export default function ScheduleWorkspace({
  schedules,
  consultations,
  draft,
  setDraft,
  onSave,
  onToggle,
  onDelete,
  onConsultation,
  onChecklistChange,
}: Props) {
  const today = hongKongToday();
  const [month, setMonth] = useState((draft?.date || today).slice(0, 7));
  const [selected, setSelected] = useState<string | null>(null);
  const [direction, setDirection] = useState("next");
  const [allApplications, setAllApplications] = useState(false);
  const [year, number] = month.split("-").map(Number);
  const visible = [...schedules]
    .filter((s) => allApplications || (selected ? s.date === selected : s.date.startsWith(month)))
    .sort((a, b) => a.date.localeCompare(b.date));
  const monthItems = schedules.filter((s) => s.date.startsWith(month));
  function shift(offset: number) {
    setDirection(offset > 0 ? "next" : "previous");
    setMonth(
      dateKey(new Date(Date.UTC(year, number - 1 + offset, 1))).slice(0, 7),
    );
    setSelected(null);
  }
  function add() {
    setDraft({
      id: crypto.randomUUID(),
      title: "",
      date: selected || "",
      notes: "",
      materials: "",
      applicationMethod: "",
      done: false,
      created: new Date().toISOString(),
    });
  }
  return (
    <section className="page calendar-page">
      <div className="calendar-page-heading">
        <div>
          <div className="eyebrow">PERSONAL PLANNING</div>
          <h1>日程安排</h1>
          <p>事项、计划日期、资料清单与备注，一目了然。</p>
          <p className="small muted">本人已提交：{schedules.filter((s) => s.applicationStatus === "submitted").length} 项 · 准备材料：{schedules.filter((s) => s.applicationStatus === "preparing").length} 项</p>
          <button className="text-button" onClick={() => setAllApplications(!allApplications)}>{allApplications ? "返回本月日程" : "查看全部申请与事项"}</button>
        </div>
        <button className="primary" onClick={add}>
          <Plus size={16} />
          新增事项
        </button>
      </div>
      <div className="schedule-workspace">
        <section className="agenda-panel" aria-label="事项与申请资料">
          <div className="agenda-heading">
            <div>
              <span className="eyebrow">你的事项</span>
              <h2>
                {allApplications ? "全部申请与事项" : selected
                  ? `${Number(selected.slice(5, 7))} 月 ${Number(selected.slice(8))} 日`
                  : "本月安排"}
                <span>{visible.length}</span>
              </h2>
            </div>
            {selected && (
              <button className="text-button" onClick={() => setSelected(null)}>
                查看整月
              </button>
            )}
          </div>
          {draft && (
            <form
              className="agenda-editor"
              onSubmit={async (e) => {
                e.preventDefault();
                if (!draft.title.trim()) return;
                if (!await onSave({ ...draft, title: draft.title.trim() })) return;
                setMonth(draft.date.slice(0, 7));
                setSelected(draft.date);
                setDraft(null);
              }}
            >
              <h3>
                {schedules.some((s) => s.id === draft.id)
                  ? "编辑事项"
                  : "新的事项"}
              </h3>
              <label>
                事项标题
                <input
                  required
                  maxLength={150}
                  value={draft.title}
                  onChange={(e) =>
                    setDraft({ ...draft, title: e.target.value })
                  }
                  placeholder="例如：核对毕业生补贴申请资料"
                />
              </label>
              <label>
                我的计划日期（Asia/Hong_Kong，非官方截止日）
                <input
                  required
                  type="date"
                  value={draft.date}
                  onChange={(e) => setDraft({ ...draft, date: e.target.value })}
                />
              </label>
              <label>
                资料清单
                <textarea
                  maxLength={2000}
                  value={draft.materials || ""}
                  onChange={(e) =>
                    setDraft({ ...draft, materials: e.target.value })
                  }
                  placeholder="记录需要准备、尚待核查的资料，每行一项"
                />
              </label>
              <label>
                备注
                <textarea
                  maxLength={1000}
                  value={[draft.notes, draft.applicationMethod].filter(Boolean).join("\n")}
                  onChange={(e) =>
                    setDraft({ ...draft, notes: e.target.value, applicationMethod: "" })
                  }
                  placeholder="其他需要记住的事情"
                />
              </label>
              <p className="small muted">
                资料与申请方法由你记录，请对照关联咨询的官方原文核实。
              </p>
              <div className="actions">
                <button className="primary">确认保存</button>
                <button type="button" onClick={() => setDraft(null)}>
                  取消
                </button>
              </div>
            </form>
          )}
          {visible.map((s) => (
            <article
              className={`agenda-item ${s.done ? "is-done" : ""}`}
              key={s.id}
            >
              <div className="agenda-item-heading">
                <button
                  className={`check ${s.done ? "done" : ""}`}
                  aria-label={`${s.done ? "标记未完成" : "标记完成"}：${s.title}`}
                  onClick={() => onToggle(s.id)}
                >
                  {s.done && <Check size={14} />}
                </button>
                <div>
                  <small>
                    {s.date.replaceAll("-", ".")}{" "}
                    <span>· {s.done ? "已完成" : "待处理"}</span>
                  </small>
                  <h3>{s.title}</h3>
                </div>
                <button className="text-button" onClick={() => setDraft(s)}>
                  编辑
                </button>
                <button
                  className="icon"
                  aria-label={`删除事项：${s.title}`}
                  onClick={() => {
                    if (confirm("删除这个日程事项？")) onDelete(s.id);
                  }}
                >
                  <Trash2 size={15} />
                </button>
              </div>
              <div className="agenda-resources">
                <p>申请状态：{({planned:"计划申请",preparing:"准备材料",submitted:"本人已提交",approved:"本人记录已获批",rejected:"本人记录未获批"} as Record<string,string>)[s.applicationStatus || "planned"]}</p>
                <section>
                  <h4>
                    <FileText size={14} />
                    资料清单
                  </h4>
                  <p className={!s.materials ? "muted" : ""}>
                    {s.materials || "尚未记录资料，点击编辑补充。"}
                  </p>
                </section>
              </div>
              {s.materialsChecklist?.length ? <section><h4>材料准备进度</h4>{s.materialsChecklist.map((m,i) => <div key={i}>
                <label><input type="checkbox" checked={m.done} onChange={() => onChecklistChange(s.id,i)} />{m.text}</label>
                {m.condition && <p className="small muted">适用情形：{m.condition}</p>}
                <details><summary>{m.source_kind === "screenshot" ? `用户截图 · ${m.location || "待核实位置"}` : "政策原文"} [{m.evidence_id}]</summary>
                  {m.source_title && <p className="small muted">{m.source_title} · 用户已核对文字，不代表官方确认</p>}<blockquote>{m.quote}</blockquote></details>
              </div>)}</section> : null}
              {s.policySources?.map((source) => <p className="small muted" key={source.evidence_id}>依据：<a href={official(source.source_url)} target="_blank" rel="noreferrer">{source.title}</a> · {source.locator} · 快照{source.version.slice(0,8)}</p>)}
              {(s.notes || s.applicationMethod) && <p className="agenda-notes">备注：{[s.notes, s.applicationMethod].filter(Boolean).join("\n")}</p>}
              {s.consultationId &&
                consultations.some((c) => c.id === s.consultationId) && (
                  <button
                    className="text-button"
                    onClick={() => onConsultation(s.consultationId!)}
                  >
                    查看关联咨询与政策原文 <ChevronRight size={13} />
                  </button>
                )}
            </article>
          ))}
          {!visible.length && !draft && (
            <div className="agenda-empty">
              <CalendarDays size={30} />
              <h3>{selected ? "这一天，还没有安排" : "本月还没有事项"}</h3>
              <p>在右侧选择日期，再记录事项和申请资料。</p>
              <button onClick={add}>
                <Plus size={15} />
                添加事项
              </button>
            </div>
          )}
          <p className="agenda-footnote">
            确认保存后同步本机数据库与浏览器；旧日程编辑保存后同步 · 无自动提醒
          </p>
        </section>
        <section className="month-calendar" aria-label="月历">
          <div className="calendar-header">
            <div>
              <span className="calendar-year">{year}</span>
              <h2>{number}月</h2>
            </div>
            <div className="calendar-controls">
              <button
                className="icon"
                aria-label="上个月"
                onClick={() => shift(-1)}
              >
                <ChevronLeft size={18} />
              </button>
              <button
                onClick={() => {
                  setMonth(today.slice(0, 7));
                  setSelected(today);
                }}
              >
                今天
              </button>
              <button
                className="icon"
                aria-label="下个月"
                onClick={() => shift(1)}
              >
                <ChevronRight size={18} />
              </button>
            </div>
          </div>
          <div className="calendar-weekdays">
            {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map(
              (day) => (
                <span key={day}>{day}</span>
              ),
            )}
          </div>
          <div key={month} className={`calendar-grid month-${direction}`}>
            {monthDays(month).map((day, index) => {
              const items = schedules.filter((s) => s.date === day.key);
              return (
                <button
                  key={day.key}
                  className={`calendar-day ${day.current ? "" : "outside"} ${index % 7 > 4 ? "weekend" : ""} ${day.key === today ? "today" : ""} ${day.key === selected ? "selected" : ""}`}
                  aria-label={`${day.key}，${items.length} 项事项${day.key === today ? "，今天" : ""}`}
                  aria-pressed={day.key === selected}
                  onClick={() => {
                    setSelected(day.key);
                    if (!day.current) setMonth(day.key.slice(0, 7));
                  }}
                >
                  <span className="day-number">{day.day}</span>
                  {items.length > 0 && (
                    <span
                      className={`event-line ${items.every((s) => s.done) ? "completed" : ""}`}
                      aria-hidden="true"
                    />
                  )}
                  <span className="day-count" aria-hidden="true">
                    {items.length > 1 ? `${items.length} 项` : ""}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="calendar-legend">
            <span>
              <i />
              有事项
            </span>
            <span>
              <i className="completed" />
              全部完成
            </span>
            <span>本月 {monthItems.length} 项</span>
          </div>
          <p className="calendar-note">
            点击日期查看左侧事项 · 日期由你确认，不推算政策截止日
          </p>
        </section>
      </div>
    </section>
  );
}
