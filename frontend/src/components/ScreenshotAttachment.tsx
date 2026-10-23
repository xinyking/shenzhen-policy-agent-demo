import { useEffect, useRef, useState } from "react";
import { ImagePlus, X } from "lucide-react";

export type ScreenshotSelection = { file: File; consent: boolean; visionModel: string };

export default function ScreenshotAttachment({ value, onChange, busy, modelName }:
  { value: ScreenshotSelection | null; onChange: (value: ScreenshotSelection | null) => void; busy: boolean; modelName: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!value) { setPreview(""); return; }
    const url = URL.createObjectURL(value.file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [value?.file]);
  return <section className="screenshot-attachment" aria-label="办事截图附件">
    <input hidden ref={input} type="file" accept="image/png,image/jpeg" aria-label="选择办事截图" onChange={(e) => {
      const file = e.target.files?.[0]; e.target.value = "";
      if (!file) return;
      if (!["image/png", "image/jpeg"].includes(file.type) || file.size > 4 * 1024 * 1024) { setError("请选择不超过4MiB的PNG/JPEG截图。"); return; }
      setError(""); onChange({ file, consent: false, visionModel: "" });
    }} />
    {!value ? <button className="text-button" disabled={busy} onClick={() => input.current?.click()}><ImagePlus size={15} />添加办事截图</button> : <>
      <div className="attachment-preview">{preview && <img src={preview} alt="待发送的办事截图预览" />}
        <span><strong>{value.file.name}</strong><small>本次发送后识别，不上传到政策库</small></span>
        <button className="icon" aria-label="移除截图" disabled={busy} onClick={() => onChange(null)}><X size={16} /></button>
      </div>
      <label className="screenshot-consent"><input type="checkbox" disabled={busy} checked={value.consent} onChange={(e) => onChange({ ...value, consent: e.target.checked })} />
        已遮挡无关个人信息，同意将此截图发送给设置中所配置的模型。识别结果保存在本机咨询，原图不写入数据库。</label>
      <details><summary>图片模型设置 · 默认使用 {modelName || "当前模型"}</summary>
        <label>视觉模型名称（与当前API地址、密钥共用；仅本次）<input maxLength={200} value={value.visionModel} onChange={(e) => onChange({ ...value, visionModel: e.target.value })} placeholder="当前模型不支持图片时，填写同一服务的视觉模型名称" /></label>
      </details>
    </>}
    {error && <p className="small danger-action" role="alert">{error}</p>}
  </section>;
}
