import { useState } from "react";
import { backgroundGroups } from "./QuickContext";

export default function FollowUp({ value, disabled, onSend }: {
  value: { question: string; fields: string[] }; disabled: boolean; onSend: (reply: string) => void;
}) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const groups = backgroundGroups.filter((group) => value.fields.includes(group.name));
  if (!groups.length) return null;
  const question = groups.every((group) => value.question.includes(group.name)) ? value.question
    : `如果你愿意进一步了解，可以补充${groups.map((group) => group.name).join("、")}。点选下面的情况，或直接回复即可。`;
  return <section className="follow-up" aria-label="继续聊聊你的情况">
    <p>{question}</p>
    {groups.map((group) => <fieldset key={group.name}><legend>{group.name}</legend>
      {group.options.map((option) => <button key={option} type="button" disabled={disabled} aria-pressed={selected[group.name] === option}
        onClick={() => setSelected((s) => ({ ...s, [group.name]: option }))}>{option}</button>)}
    </fieldset>)}
    <button className="text-button" disabled={disabled || !Object.keys(selected).length} onClick={() => onSend(groups.filter((g) => selected[g.name]).map((g) => `${g.name}：${selected[g.name]}`).join("；"))}>发送补充信息，继续了解 →</button>
    <small>仅用于本次咨询的进一步解释，不作资格认定。也可以直接在输入框回复。</small>
  </section>;
}
