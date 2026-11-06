import { useState } from "react";
import { IcCheck, IcCopy } from "./icons.jsx";

/** Maxfiy kalitni ko'rsatish + nusxalash (ISUP kalitlari). */
export default function KeyBox({ value, note }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard yo'q */ }
  };
  return (
    <div className="isup-keybox">
      <code>{value}</code>
      <button type="button" className="btn-sq" title="Nusxalash" onClick={copy}>{copied ? <IcCheck size={15} /> : <IcCopy size={15} />}</button>
      {note && <div className="cf-hint" style={{ flexBasis: "100%", margin: "6px 0 0" }}>{note}</div>}
    </div>
  );
}
