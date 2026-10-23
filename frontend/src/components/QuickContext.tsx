export const backgroundGroups = [
  { name: "学历", options: ["本科", "硕士", "博士", "其他"] },
  { name: "毕业时间", options: ["在读", "毕业2年内", "毕业超过2年"] },
  { name: "就业状态", options: ["待业", "已入职", "灵活就业", "就业见习"] },
  { name: "深圳社保", options: ["未缴纳", "缴纳不足3个月", "连续缴纳满3个月", "不确定"] },
  { name: "在深住房", options: ["无自有住房", "有自有住房", "不确定"] },
  { name: "住房支持", options: ["未享受住房优惠", "正在享受住房优惠", "不确定"] },
  { name: "公积金", options: ["正在缴存", "未缴存", "不确定"] },
  { name: "医保类型", options: ["职工医保", "居民医保", "未参保", "不确定"] },
  { name: "就医地点", options: ["深圳市内", "深圳市外", "尚未就医"] },
];
export function backgroundText(values: Record<string, string>) {
  return backgroundGroups.filter((g) => values[g.name]).map((g) => `${g.name}：${values[g.name]}`).join("；");
}
export default function QuickContext({ values, onChange, question = "" }: {
  values: Record<string, string>; onChange: (values: Record<string, string>) => void;
  question?: string;
}) {
  const housing = /住房|租房|公积金|安居|公租/.test(question);
  const medical = /医保|医疗|看病|就医|生育/.test(question);
  const employment = /就业|毕业|人才|求职|技能|见习|失业/.test(question);
  const groups = backgroundGroups.filter((g, index) => values[g.name] ||
    (index < 4 && ((!housing && !medical) || index === 3 || employment)) || (housing && index >= 4 && index <= 6) || (medical && index >= 7));
  return <details className="quick-context">
    <summary>可选个人背景{Object.keys(values).length ? ` · 已选${Object.keys(values).length}项` : "（不填写也可查询）"}</summary>
    <p className="small muted">默认直接说明政策对象、流程与材料，不需要提供个人信息。只有希望结合自身情况解释时才选择；已选内容随下一条问题发送。</p>
    {groups.map((group) => <fieldset key={group.name}>
      <legend>{group.name}</legend>
      {group.options.map((option) => <button type="button" key={option} aria-pressed={values[group.name] === option}
        onClick={() => { const next = { ...values }; if (next[group.name] === option) delete next[group.name]; else next[group.name] = option; onChange(next); }}>{option}</button>)}
    </fieldset>)}
    {Object.keys(values).length > 0 && <button type="button" className="text-button" onClick={() => onChange({})}>清除已选背景</button>}
  </details>;
}
