import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api/client";
import { relTime, statusColor } from "../lib/format";
import { PageHeader } from "../components/ui.jsx";
import { IcCamera, IcCheck, IcCopy, IcDownload, IcFile, IcPause, IcPlay, IcRefresh, IcSearch, IcWarn } from "../components/icons.jsx";

// Server xotirasidagi ring-buffer (logbuf): INFO+ , oxirgi 1000 yozuv, qayta ishga tushganda tozalanadi
const BUF_CAP = 1000;
const LVL = {
  ERROR: { c: "#f2585b", bg: "rgba(242,88,91,.14)", t: "Xato" },
  CRITICAL: { c: "#f2585b", bg: "rgba(242,88,91,.2)", t: "Kritik" },
  WARNING: { c: "#f5b13f", bg: "rgba(245,177,63,.14)", t: "Ogohl." },
  INFO: { c: "#5cc1f7", bg: "rgba(92,193,247,.13)", t: "Info" },
  DEBUG: { c: "var(--faint)", bg: "rgba(127,127,127,.12)", t: "Debug" },
};
const TABS = [["ALL", "Hammasi"], ["WARNING", "Ogohlantirish"], ["ERROR", "Xato"], ["INFO", "Info"]];
const isErr = (l) => l === "ERROR" || l === "CRITICAL";
const ts = (t) => { try { return new Date(t * 1000).toLocaleTimeString("ru-RU"); } catch { return ""; } };
const full = (t) => { try { return new Date(t * 1000).toLocaleString("ru-RU"); } catch { return ""; } };
const src = (l) => (l || "").replace("vizora.", "");
const lineCls = (line) => /error|xato|fail|uzildi|key_error|401|not permitted|traceback/i.test(line) ? "err"
  : /warn|ogohlan/i.test(line) ? "warn"
    : /ishga tushdi|muvaffaqiyatli|ready|boshlandi|tayyor/i.test(line) ? "ok" : "";

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a"); a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

function CopyBtn({ text }) {
  const [done, setDone] = useState(false);
  return (
    <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1400); }}>
      {done ? <IcCheck size={14} /> : <IcCopy size={14} />}{done ? "Olindi" : "Nusxa"}
    </button>
  );
}

export default function Logs() {
  const [records, setRecords] = useState([]);
  const [level, setLevel] = useState("ALL");
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [auto, setAuto] = useState(true);
  const [err, setErr] = useState(null);
  const [selected, setSelected] = useState(null);

  const loadLogs = useCallback(() =>
    apiFetch(`/monitoring/logs?level=ALL&limit=${BUF_CAP}`)
      .then((d) => { setRecords(d.records || []); setErr(null); })
      .catch((e) => setErr(e.message)), []);
  useEffect(() => { loadLogs(); }, [loadLogs]);
  useEffect(() => { if (!auto) return; const id = setInterval(loadLogs, 3000); return () => clearInterval(id); }, [auto, loadLogs]);

  const counts = useMemo(() => {
    const c = { ALL: records.length, INFO: 0, WARNING: 0, ERROR: 0 };
    for (const r of records) { if (isErr(r.level)) c.ERROR++; else if (c[r.level] != null) c[r.level]++; }
    return c;
  }, [records]);
  // Eng ko'p yozadigan manbalar — filtr chiplari
  const sources = useMemo(() => {
    const m = {};
    for (const r of records) m[src(r.logger)] = (m[src(r.logger)] || 0) + 1;
    return Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [records]);

  // Filtr + eng yangisi tepada + ketma-ket takrorlar bitta qatorga (×N)
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    const out = [];
    for (let i = records.length - 1; i >= 0; i--) {
      const r = records[i];
      if (level === "ERROR" ? !isErr(r.level) : level !== "ALL" && r.level !== level) continue;
      if (source && src(r.logger) !== source) continue;
      if (n && !(r.msg || "").toLowerCase().includes(n) && !src(r.logger).toLowerCase().includes(n)) continue;
      const prev = out[out.length - 1];
      if (prev && prev.msg === r.msg && prev.logger === r.logger && prev.level === r.level) { prev.n += 1; prev.first = r.ts; continue; }
      out.push({ ...r, n: 1, first: r.ts });
    }
    return out;
  }, [records, level, source, q]);

  const oldest = records[0]?.ts;
  const exportText = () => rows.map((r) => `${full(r.ts)}\t${r.level}\t${src(r.logger)}\t${r.msg}${r.n > 1 ? `  (×${r.n})` : ""}`).join("\n");

  // ===== Kamera (publisher) logi =====
  const [cams, setCams] = useState([]);
  const [camQ, setCamQ] = useState("");
  const [camId, setCamId] = useState("");
  const [camLines, setCamLines] = useState(null);
  const [camMsg, setCamMsg] = useState("");
  const [onlyErr, setOnlyErr] = useState(false);
  useEffect(() => { apiFetch("/cameras?source_type=ezviz&limit=1000&sort=status").then((d) => setCams(Array.isArray(d) ? d : [])).catch(() => {}); }, []);
  const camOpts = useMemo(() => {
    const n = camQ.trim().toLowerCase();
    return cams.filter((c) => !n || c.name.toLowerCase().includes(n)).slice(0, 80);
  }, [cams, camQ]);
  const loadCamLog = useCallback((id) => {
    if (!id) { setCamLines(null); setCamMsg(""); return; }
    apiFetch(`/monitoring/camera-log/${id}?lines=400`).then((d) => {
      if (!d.exists) { setCamLines([]); setCamMsg("Bu kamera uchun log yo'q — u hali ochilmagan (log birinchi oqimda paydo bo'ladi)."); }
      else { setCamLines(d.lines || []); setCamMsg(""); }
    }).catch((e) => setCamMsg(e.message));
  }, []);
  useEffect(() => {
    if (!camId) return;
    loadCamLog(camId);
    const id = setInterval(() => loadCamLog(camId), 4000);
    return () => clearInterval(id);
  }, [camId, loadCamLog]);
  const camShown = useMemo(() => (camLines || []).filter((l) => !onlyErr || lineCls(l) === "err").slice().reverse(), [camLines, onlyErr]);
  const camSel = cams.find((c) => c.id === camId);

  return (
    <div>
      <PageHeader
        eyebrow="Diagnostika"
        title="Loglar"
        subtitle="Server hodisalari va kameralar oqim (publisher) loglari — xatoni topish va sababini ko'rish uchun."
        tag={null}
        rightSlot={
          <div className={`sys-pill ${auto ? "ok" : ""}`} title={auto ? "Har 3 soniyada yangilanadi" : "Avto yangilash to'xtatilgan"}>
            <span className="dot" />{auto ? "Jonli" : "To'xtatilgan"}
          </div>
        }
      />

      <div className="ent-summary card">
        <div className="es"><span className="v">{counts.ALL}</span><span className="l">yozuv</span><em>{oldest ? `${ts(oldest)} dan beri` : "—"}</em></div>
        <div className="es"><span className="v" style={counts.ERROR ? { color: "#f2585b" } : undefined}>{counts.ERROR}</span><span className="l">xato</span></div>
        <div className="es"><span className="v" style={counts.WARNING ? { color: "#f5b13f" } : undefined}>{counts.WARNING}</span><span className="l">ogohlantirish</span></div>
        <div className="es"><span className="v">{counts.INFO}</span><span className="l">info</span></div>
      </div>

      {/* Tizim loglari */}
      <div className="card clip logs-card">
        <div className="cam-filters">
          <div className="search-inline">
            <IcSearch size={15} />
            <input className="input" placeholder="Xabar yoki manba bo'yicha qidirish…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <button className={`btn ${auto ? "soft" : "ghost"}`} onClick={() => setAuto((v) => !v)} title="Avto yangilash">
            {auto ? <><IcPause size={14} />To'xtatish</> : <><IcPlay size={14} />Jonli</>}
          </button>
          <button className="btn-sq" title="Hozir yangilash" onClick={loadLogs}><IcRefresh size={15} /></button>
          <button className="btn-sq" title="Ko'rinayotgan loglarni yuklab olish (.txt)" onClick={() => download(`server-log-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.txt`, exportText())}><IcDownload size={15} /></button>
        </div>
        <div className="list-tabs" role="tablist">
          {TABS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={level === k} className={`lt${level === k ? " on" : ""}${k === "ERROR" && counts.ERROR ? " warn" : ""}`} onClick={() => setLevel(k)}>
              {l}<span className="n">{counts[k]}</span>
            </button>
          ))}
        </div>
        {sources.length > 1 && (
          <div className="log-srcs">
            <span className="cap">Manba:</span>
            <button className={`chip${!source ? " accent" : ""}`} onClick={() => setSource("")}>hammasi</button>
            {sources.map(([s, n]) => (
              <button key={s} className={`chip${source === s ? " accent" : ""}`} onClick={() => setSource(source === s ? "" : s)}>{s}<em>{n}</em></button>
            ))}
          </div>
        )}

        {err && <div className="error-banner" style={{ margin: 12 }}>{err}</div>}
        <div className="log-list">
          {rows.map((r, i) => {
            const s = LVL[r.level] || LVL.INFO;
            return (
              <button key={`${r.ts}-${i}`} className={`log-row${selected === r ? " sel" : ""}`} style={{ "--lc": s.c }} onClick={() => setSelected(r)}>
                <span className="t mono" title={full(r.ts)}>{ts(r.ts)}</span>
                <span className="lv" style={{ color: s.c, background: s.bg }}>{s.t}</span>
                <span className="sr mono" title={r.logger}>{src(r.logger)}</span>
                <span className="m mono">{r.msg}{r.n > 1 && <b className="rep" title={`${ts(r.first)} – ${ts(r.ts)}`}>×{r.n}</b>}</span>
              </button>
            );
          })}
          {rows.length === 0 && <div className="empty">{records.length ? "Filtrga mos log yo'q." : "Log yo'q."}</div>}
        </div>
        <div className="log-foot">
          Server xotirasidagi oxirgi {BUF_CAP} yozuv (INFO va undan yuqori) — server qayta ishga tushganda tozalanadi.
          Doimiy tarix uchun: <span className="mono">docker compose logs app</span>
        </div>
      </div>

      {/* Pastki: kamera logi + tafsilot */}
      <div className="rgrid logs-row" style={{ "--cols": "minmax(0,1.4fr) minmax(0,1fr)" }}>
        <div className="card clip">
          <div className="card-head wrap">
            <div>
              <div className="card-title"><span className="ci"><IcCamera size={16} /></span>Kamera oqim logi</div>
              <div className="card-sub ind">EZVIZ / Hik kameraning publisher logi — "nega ochilmadi" savoliga javob.</div>
            </div>
          </div>
          <div className="cam-filters">
            <div className="search-inline">
              <IcSearch size={15} />
              <input className="input" placeholder="Kamera nomi…" value={camQ} onChange={(e) => setCamQ(e.target.value)} />
            </div>
            <select className="select" value={camId} onChange={(e) => setCamId(e.target.value)}>
              <option value="">{camOpts.length ? `Kamerani tanlang (${camOpts.length})` : "Topilmadi"}</option>
              {camOpts.map((c) => <option key={c.id} value={c.id}>{c.status === "online" ? "● " : "○ "}{c.name}</option>)}
            </select>
          </div>
          {camId && (
            <div className="log-camhead">
              <span className="dot" style={{ background: statusColor(camSel?.status) }} />
              <b>{camSel?.name}</b>
              <span className="faint">{camSel?.last_seen_at ? `oxirgi aloqa ${relTime(camSel.last_seen_at)}` : ""}</span>
              <div className="spacer" />
              <label className="wall-check"><input type="checkbox" checked={onlyErr} onChange={(e) => setOnlyErr(e.target.checked)} />Faqat xatolar</label>
              {camLines?.length > 0 && (
                <button className="btn-sq" title="Logni yuklab olish" onClick={() => download(`kamera-${(camSel?.name || camId).replace(/[^\w.-]+/g, "_")}.log`, camLines.join("\n"))}><IcDownload size={14} /></button>
              )}
            </div>
          )}
          <div className="log-cam">
            {!camId && (
              <div className="empty" style={{ padding: "46px 20px" }}>
                <IcCamera size={36} sw={1.3} />
                <div style={{ fontWeight: 600, color: "var(--dim)", marginTop: 8, fontSize: 14.5 }}>Diagnostika uchun kamera tanlang</div>
                <div style={{ fontSize: 12.5, marginTop: 4 }}>Eng yangi qatorlar tepada, har 4 soniyada yangilanadi.</div>
              </div>
            )}
            {camId && camMsg && <div className="empty">{camMsg}</div>}
            {camId && !camMsg && camLines === null && <div className="empty">Yuklanmoqda…</div>}
            {camShown.map((line, i) => <div key={i} className={`cl ${lineCls(line)}`}>{line}</div>)}
            {camId && camLines?.length > 0 && camShown.length === 0 && <div className="empty">Xato qatorlari yo'q.</div>}
          </div>
        </div>

        <div className="card clip">
          <div className="card-head">
            <div>
              <div className="card-title"><span className="ci"><IcFile size={16} /></span>Yozuv tafsiloti</div>
              <div className="card-sub ind">Ro'yxatdan yozuvni tanlang.</div>
            </div>
          </div>
          {selected ? (() => {
            const s = LVL[selected.level] || LVL.INFO;
            return (
              <div className="log-detail">
                <div className="ld-top">
                  <span className="lv" style={{ color: s.c, background: s.bg }}>{isErr(selected.level) && <IcWarn size={12} />}{selected.level}</span>
                  <span className="mono faint">{full(selected.ts)}</span>
                  {selected.n > 1 && <span className="chip">×{selected.n} marta ({ts(selected.first)} – {ts(selected.ts)})</span>}
                </div>
                <div className="label">Manba</div>
                <div className="mono" style={{ fontSize: 13, marginBottom: 14, wordBreak: "break-all" }}>{selected.logger}</div>
                <div className="ld-bar"><span className="label" style={{ margin: 0 }}>Xabar</span><CopyBtn text={`${full(selected.ts)} ${selected.level} ${selected.logger}: ${selected.msg}`} /></div>
                <pre className="codeblock" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0 }}>{selected.msg}</pre>
                <button className="link-btn" style={{ marginTop: 12 }} onClick={() => { setSource(src(selected.logger)); setLevel("ALL"); }}>Shu manbaning barcha yozuvlari →</button>
              </div>
            );
          })() : (
            <div className="empty" style={{ padding: "56px 20px" }}>
              <IcFile size={36} sw={1.3} />
              <div style={{ fontWeight: 600, color: "var(--dim)", marginTop: 8, fontSize: 14.5 }}>Yozuv tanlanmagan</div>
              <div style={{ fontSize: 12.5, marginTop: 4 }}>To'liq vaqt, manba va xabarni nusxalash shu yerda.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
