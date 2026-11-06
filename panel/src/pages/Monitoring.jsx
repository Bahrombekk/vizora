import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import { csvCell, relTime, statusColor, statusLabel } from "../lib/format";
import { PageHeader, MiniArea } from "../components/ui.jsx";
import Player from "../player/Player";
import Modal from "../components/Modal";
import Pager from "../components/Pager";
import ActivityChart, { chartStats } from "../components/ActivityChart";
import {
  IcActivity, IcCamera, IcChart, IcClock, IcCpu, IcDatabase,
  IcExport, IcEye, IcFile, IcRefresh, IcSearch, IcServer, IcSort, IcVideo, IcWarn,
} from "../components/icons.jsx";

// Nosozlik sabablari ([[manager.FAULT_NO_STREAM]]).
const FAULT_REASON = { no_stream: "Video bermadi", missing: "Bulutda yo'q" };
const RANGES = [["24h", "24 soat"], ["7d", "7 kun"], ["30d", "30 kun"]];
const PAGE = 20;
// Ro'yxat tablari. "Muammoli" = oqim yo'q + xatolik (qurilma bor-u video yo'q).
const TABS = [["all", "Hammasi"], ["problem", "Muammoli"], ["online", "Onlayn"], ["offline", "Oflayn"]];
const inTab = (tab, st) => tab === "all" || (tab === "problem" ? st === "no_stream" || st === "error" : st === tab);
// Saralashda holat tartibi: muammolilar tepada
const STATUS_ORDER = { no_stream: 0, error: 1, offline: 2, online: 3 };


const pad = (n) => String(n).padStart(2, "0");
const clock = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
function uptimeText(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} kun ${h} soat` : h ? `${h} soat ${m} daq` : `${m} daq`;
}
// Resurs bandligi -> holat yorlig'i va rangi
const level = (v) => v == null ? { t: "—", c: "var(--faint)" }
  : v >= 85 ? { t: "Yuqori", c: "var(--danger)" }
  : v >= 65 ? { t: "O'rtacha", c: "var(--warn)" }
  : { t: "Yaxshi", c: "var(--online)" };

export default function Monitoring() {
  const [cams, setCams] = useState([]);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [, setTick] = useState(0);
  const [sys, setSys] = useState(null);
  const [health, setHealth] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [faults, setFaults] = useState(null);
  const [logFor, setLogFor] = useState(null);
  const [logLines, setLogLines] = useState(null);
  const [rng, setRng] = useState("24h");
  const [hist, setHist] = useState(null);
  const [watch, setWatch] = useState(null);
  const [sort, setSort] = useState({ key: "status", dir: 1 });
  const [page, setPage] = useState(0);
  const esRef = useRef(null);
  const listRef = useRef(null);

  const loadCams = () =>
    apiFetch("/monitoring/cameras").then((d) => { setCams(d || []); setErr(null); })
      .catch((e) => setErr(e.message)).finally(() => setLoading(false));
  const loadSys = () => Promise.all([
    apiFetch("/monitoring/system").then(setSys).catch(() => {}),
    apiFetch("/dashboard/health").then(setHealth).catch(() => setHealth(null)),
  ]).then(() => setUpdatedAt(new Date()));

  useEffect(() => { loadCams(); }, []);

  useEffect(() => {
    loadSys();
    const id = setInterval(loadSys, 15000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let stop = false;
    const load = () => apiFetch("/monitoring/faults").then((d) => { if (!stop) setFaults(d); }).catch(() => {});
    load();
    const id = setInterval(load, 20000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  useEffect(() => {
    let stop = false;
    const load = () => apiFetch(`/dashboard/history?range=${rng}&tz=${-new Date().getTimezoneOffset()}`)
      .then((d) => { if (!stop) setHist(d); }).catch(() => {});
    load();
    const id = setInterval(load, 15000);
    return () => { stop = true; clearInterval(id); };
  }, [rng]);

  // Holat o'zgarishlari real vaqtda (SSE) — ro'yxat qayta yuklanmaydi
  useEffect(() => {
    const es = new EventSource("/api/monitoring/stream", { withCredentials: true });
    esRef.current = es;
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (!m || !m.camera_id) return;
      const nowIso = new Date().toISOString();
      setCams((prev) => prev.map((c) => c.id === m.camera_id ? { ...c, status: m.status, last_seen_at: m.status === "online" ? nowIso : c.last_seen_at } : c));
    };
    return () => es.close();
  }, []);

  // "N daq oldin" yozuvlari yangilanib tursin
  useEffect(() => { const id = setInterval(() => setTick((t) => t + 1), 15000); return () => clearInterval(id); }, []);

  const counts = useMemo(() => {
    const c = { online: 0, offline: 0, error: 0, no_stream: 0 };
    for (const cam of cams) c[cam.status] = (c[cam.status] || 0) + 1;
    return c;
  }, [cams]);
  const total = cams.length;
  const tabCount = (t) => cams.reduce((n, c) => n + (inTab(t, c.status) ? 1 : 0), 0);
  const goPage = (p) => {
    setPage(p);
    const el = listRef.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = cams.filter((c) => {
      if (!inTab(filter, c.status)) return false;
      if (needle && ![c.name, c.zone_name, c.branch_name, c.location].some((x) => (x || "").toLowerCase().includes(needle))) return false;
      return true;
    });
    const k = sort.key, d = sort.dir;
    list.sort((a, b) => {
      let r = 0;
      if (k === "name") r = (a.name || "").localeCompare(b.name || "");
      else if (k === "status") r = (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9);
      else if (k === "seen") r = new Date(b.last_seen_at || 0) - new Date(a.last_seen_at || 0);
      if (r === 0) r = (a.name || "").localeCompare(b.name || "");
      return r * d;
    });
    return list;
  }, [cams, filter, q, sort]);
  useEffect(() => { setPage(0); }, [filter, q, sort]);
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const rows = shown.slice(page * PAGE, page * PAGE + PAGE);

  // ===== Server holati (haqiqiy) =====
  const now = sys?.now || {};
  const histSys = sys?.history || [];
  const cpuL = level(now.cpu), memL = level(now.mem);
  const services = health ? [
    ["Video server", health.mediamtx], ["Baza", health.database], ["Redis", health.redis], ["Holat tekshiruvi", health.probe],
  ] : [];
  const bad = services.filter(([, ok]) => ok === false).map(([n]) => n);
  const healthText = !health ? "Tekshirilmoqda…" : bad.length ? `Muammo: ${bad.join(", ")}` : "Barcha xizmatlar ishlayapti";

  const tiles = [
    {
      icon: <IcCpu size={17} />, lbl: "CPU bandligi", tag: cpuL.t, tagColor: cpuL.c,
      val: now.cpu != null ? <>{Math.round(now.cpu)}<small>%</small></> : "—", valColor: now.cpu != null ? cpuL.c : "var(--text)",
      sub: `${sys?.cores || "?"} yadro${now.load1 != null ? ` · yuklama ${now.load1}` : ""}`,
      spark: histSys.map((s) => s.cpu).filter((v) => v != null), color: "var(--online)",
    },
    {
      icon: <IcDatabase size={17} />, lbl: "Xotira (RAM)", tag: memL.t, tagColor: memL.c,
      val: now.mem != null ? <>{Math.round(now.mem)}<small>%</small></> : "—", valColor: "var(--text)",
      sub: now.mem_total_gb ? `${now.mem_used_gb} / ${now.mem_total_gb} GB` : "—",
      spark: histSys.map((s) => s.mem).filter((v) => v != null), color: "var(--info)",
    },
    {
      icon: <IcVideo size={17} />, lbl: "Faol oqimlar", tag: sys?.viewing ? `${sys.viewing} ko'rilmoqda` : "Bo'sh", tagColor: sys?.viewing ? "var(--accent)" : "var(--faint)",
      val: sys?.streams ?? "—", valColor: "var(--text)",
      sub: sys ? `${sys.viewers} tomoshabin · bulut publisherlari` : "—",
    },
    {
      icon: <IcClock size={17} />, lbl: "Ish vaqti", tag: health?.probe_age != null ? `tekshiruv ${health.probe_age} s oldin` : "", tagColor: "var(--faint)",
      val: <span style={{ fontSize: 21 }}>{uptimeText(sys?.uptime)}</span>, valColor: "var(--text)",
      sub: "Server oxirgi marta yoqilganidan beri",
    },
  ];

  const stats = [
    { f: "all", icon: <IcCamera size={16} />, iconClass: "", lbl: "Hammasi", val: total, sub: "Jami kameralar", color: "var(--accent)" },
    { f: "online", icon: <IcActivity size={16} />, iconClass: "ok", lbl: "Onlayn", val: counts.online, extra: `${pct(counts.online)}%`, extraColor: "var(--online)", sub: "Faol kameralar", color: "var(--online)" },
    // ALOHIDA KATEGORIYA: tarmoqda bor-u video bermaydi ([[probe.no_stream]]).
    { f: "problem", icon: <IcWarn size={16} />, iconClass: "err", lbl: "Oqim yo'q", val: counts.no_stream, extra: `${pct(counts.no_stream)}%`, extraColor: "var(--error)", sub: "Video kelmaydi", color: "var(--error)" },
    { f: "offline", icon: <IcCamera size={16} />, iconClass: "off", lbl: "Oflayn", val: counts.offline, extra: `${pct(counts.offline)}%`, extraColor: "var(--dim)", sub: "Hozirda aloqasiz", color: "var(--offline)" },
  ];
  if (counts.error) stats.push({ f: "problem", icon: <IcWarn size={16} />, iconClass: "err", lbl: "Xatolik", val: counts.error, extra: `${pct(counts.error)}%`, extraColor: "var(--error)", sub: "Nosoz kameralar", color: "var(--danger)" });
  const cs = chartStats(hist?.points || []);

  function exportCsv() {
    const rowsCsv = [["Nom", "Holat", "Filial", "Hudud", "Joylashuv", "Oxirgi ko'rish"], ...shown.map((c) => [c.name, statusLabel(c.status), c.branch_name || "", c.zone_name || "", c.location || "", c.last_seen_at || ""])];
    const csv = "﻿" + rowsCsv.map((r) => r.map(csvCell).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `monitoring-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  }

  // Kameraning publisher logi — "nega ochilmadi" degan savolga javob.
  function openLog(c) {
    setLogFor(c);
    setLogLines(null);
    apiFetch(`/monitoring/camera-log/${c.id}?lines=120`)
      .then((d) => setLogLines(d?.lines || []))
      .catch(() => setLogLines([]));
  }

  const th = (key, label) => (
    <th className="th-sort" onClick={() => setSort((s) => ({ key, dir: s.key === key ? -s.dir : 1 }))}>
      {label}<span className={`sort-ic${sort.key === key ? " on" : ""}`}><IcSort size={12} /></span>
    </th>
  );

  return (
    <div>
      <PageHeader
        eyebrow="Monitoring"
        title="Kameralar holati"
        subtitle="Kameralar va server holati real vaqtda — o'zgarishlar sahifani yangilamasdan keladi."
        tag={null}
        rightSlot={
          <div className={`sys-pill ${live ? "ok" : ""}`} title="Holat o'zgarishlari jonli oqimi (SSE)">
            <span className="dot" />{live ? "Jonli ulanish" : "Ulanmoqda…"}
          </div>
        }
      />

      {/* Server holati */}
      <div className="card pad mon-server">
        <div className="sec-head">
          <div className="card-title"><span className="ci"><IcServer size={16} /></span>Server holati</div>
          <div className="spacer" />
          <span className={`badge ${!health ? "off" : bad.length ? "err" : "on"}`}><span className="dot" />{healthText}</span>
          <span className="upd">Yangilandi: {updatedAt ? clock(updatedAt) : "—"}</span>
          <button className="icon-btn" style={{ width: 34, height: 34 }} title="Yangilash" onClick={() => { loadSys(); loadCams(); }}>
            <IcRefresh size={15} />
          </button>
        </div>
        <div className="res-grid">
          {tiles.map((t) => (
            <div className="tile res-tile" key={t.lbl}>
              <div className="rt-body">
                <div className="t-top">
                  <div className="t-ico">{t.icon}</div>
                  <span className="rt-tag" style={{ color: t.tagColor }}>{t.tag}</span>
                </div>
                <div className="t-lbl">{t.lbl}</div>
                <div className="t-val" style={{ color: t.valColor }}>{t.val}</div>
                <div className="rt-sub">{t.sub}</div>
              </div>
              {t.spark && (
                <div className="rt-spark">
                  {t.spark.length > 1
                    ? <MiniArea data={t.spark} height={44} color={t.color} fillId={`rs-${t.lbl.length}`} />
                    : <span>so'nggi 1 soat grafigi yig'ilmoqda…</span>}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Statistika + grafik */}
      <div className="rgrid mon-row" style={{ "--cols": "minmax(0,1fr) minmax(0,1.25fr)" }}>
        <div className="card pad">
          <div className="card-title" style={{ marginBottom: 2 }}><span className="ci"><IcCamera size={16} /></span>Kameralar statistikasi</div>
          <div className="card-sub ind" style={{ marginBottom: 14 }}>Kartani bosing — ro'yxat shu holat bo'yicha filtrlanadi</div>
          {/* Holat taqsimoti — bitta chiziqda */}
          <div className="dist-bar" title="Holatlar ulushi">
            {stats.filter((s) => s.f !== "all" && s.val > 0).map((s) => (
              <span key={s.lbl} style={{ flexGrow: s.val, background: s.color }} title={`${s.lbl}: ${s.val}`} />
            ))}
          </div>
          <div className="stat-grid">
            {stats.map((st) => (
              <button key={st.lbl} className={`tile stat-tile${filter === st.f ? " on" : ""}`} onClick={() => setFilter(st.f)}>
                <div className="t-top" style={{ marginBottom: 10 }}>
                  <div className={`t-ico kpi-ico ${st.iconClass}`}>{st.icon}</div>
                  {st.extra && <span style={{ fontSize: 13, fontWeight: 700, color: st.extraColor }}>{st.extra}</span>}
                </div>
                <div className="t-lbl">{st.lbl}</div>
                <div className="t-val">{st.val}</div>
                <div className="rt-sub">{st.sub}</div>
              </button>
            ))}
          </div>
        </div>

        <div className="card mon-chart">
          <div className="card-head wrap ac-head">
            <div>
              <div className="card-title"><span className="ci"><IcChart size={16} /></span>Onlayn kameralar</div>
              <div className="card-sub ind">
                {cs ? <>o'rtacha <b>{cs.avg}</b> · eng kam <b>{cs.min}</b> · eng ko'p <b>{cs.max}</b></> : "Vaqt bo'yicha"}
              </div>
            </div>
            <div className="seg">
              {RANGES.map(([k, l]) => <button key={k} className={rng === k ? "on" : ""} onClick={() => setRng(k)}>{l}</button>)}
            </div>
          </div>
          <div className="chart-wrap">
            <div className="ac-legend">
              <span><i className="sw bar" />Onlayn</span>
              {cs && <span><i className="sw avg" />O'rtacha {cs.avg}</span>}
            </div>
            <ActivityChart points={hist?.points || []} since={hist?.since} partial={hist?.partial} />
          </div>
        </div>
      </div>

      {/* NOSOZLIK KATEGORIYASI — "oqim yo'q" kameralar, sababi bilan */}
      {faults && faults.count > 0 && (
        <div className="card clip fault-card">
          <div className="card-head wrap">
            <div>
              <div className="card-title"><span className="ci err"><IcWarn size={16} /></span>Oqim yo'q ({faults.count})</div>
              <div className="card-sub ind">
                Qurilma tarmoqda bor-u video bermayapti. Har {Math.round((faults.recheck_seconds || 900) / 60)} daqiqada
                avtomatik qayta tekshiriladi — tuzalgani ro'yxatdan o'zi chiqadi.
              </div>
            </div>
            <button className="btn ghost" onClick={() => { setFilter("problem"); listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }}><IcCamera size={16} />Ro'yxatda ko'rsatish</button>
          </div>
          <div className="table-scroll">
            <table className="table">
              <thead><tr>
                <th>Kamera</th><th>Sabab</th><th>Urinish</th><th>Oxirgi ko'rish</th><th>Filial / Hudud</th><th style={{ textAlign: "right" }}>Harakatlar</th>
              </tr></thead>
              <tbody>
                {faults.cameras.map((c) => (
                  <tr key={c.id}>
                    <td style={{ fontWeight: 600 }}>{c.name}</td>
                    <td><span className="badge err"><span className="dot" />{FAULT_REASON[c.reason] || c.reason}</span></td>
                    <td className="muted">{c.tries ? `${c.tries} marta` : "—"}</td>
                    <td className="muted">{relTime(c.last_seen_at)}</td>
                    <td className="muted">{[c.branch_name, c.zone_name].filter(Boolean).join(" · ") || "—"}</td>
                    <td>
                      <div className="row-actions">
                        <button className="btn-sq" style={{ width: 32, height: 32 }} title="Ko'rish" onClick={() => setWatch(c)}><IcEye size={15} /></button>
                        <button className="btn-sq" style={{ width: 32, height: 32 }} title="Oqim logi" onClick={() => openLog(c)}><IcFile size={15} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Kameralar ro'yxati */}
      <div className="card clip" ref={listRef} style={{ scrollMarginTop: 80 }}>
        <div className="card-head wrap">
          <div>
            <div className="card-title"><span className="ci"><IcCamera size={16} /></span>Kameralar ro'yxati</div>
            <div className="card-sub ind">Sahifada {PAGE} ta kamera</div>
          </div>
          <div className="list-tools">
            <div className="search-inline">
              <IcSearch size={15} />
              <input className="input" placeholder="Kamera, hudud, filial yoki joylashuv…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            {/* Telefonda ustun sarlavhalari yo'q (kartalar) — saralash shu yerda */}
            <select className="select mob-sort" value={`${sort.key}:${sort.dir}`} onChange={(e) => { const [key, dir] = e.target.value.split(":"); setSort({ key, dir: +dir }); }}>
              <option value="status:1">Muammolilar tepada</option>
              <option value="name:1">Nom (A→Z)</option>
              <option value="seen:1">Yaqinda ko'rilgan</option>
              <option value="seen:-1">Uzoq ko'rilmagan</option>
            </select>
            <button className="btn ghost exp-btn" onClick={exportCsv} title="Filtrlangan ro'yxat, CSV (Excel)"><IcExport size={16} /><span className="lbl">Eksport</span></button>
          </div>
        </div>

        <div className="list-tabs" role="tablist">
          {TABS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={filter === k} className={`lt${filter === k ? " on" : ""}${k === "problem" ? " warn" : ""}`} onClick={() => setFilter(k)}>
              {l}<span className="n">{tabCount(k)}</span>
            </button>
          ))}
        </div>

        {err && <div className="empty" style={{ color: "var(--danger)" }}>{err}</div>}
        {!err && loading && <div className="empty">Yuklanmoqda…</div>}
        {!err && !loading && (
          <>
            <div className="table-scroll">
              <table className="table mon-table">
                <thead><tr>
                  <th style={{ width: 48 }}>#</th>
                  {th("name", "Kamera nomi")}
                  {th("status", "Holat")}
                  <th>Filial / Hudud</th>
                  <th>Joylashuv</th>
                  {th("seen", "Oxirgi ko'rish")}
                  <th style={{ textAlign: "right" }}>Harakatlar</th>
                </tr></thead>
                <tbody>
                  {rows.map((c, i) => (
                    <tr key={c.id} className={c.enabled === false ? "disabled" : ""}>
                      <td className="idx">{page * PAGE + i + 1}</td>
                      <td>
                        <div className="cam-cell">
                          <span className="nm">{c.name}</span>
                          <span className="src">{c.source_type === "ezviz" ? "EZVIZ" : "RTSP"}{c.enabled === false ? " · o'chirilgan" : ""}</span>
                        </div>
                      </td>
                      <td className="st"><span className={`badge ${c.status === "online" ? "on" : (c.status === "error" || c.status === "no_stream") ? "err" : "off"}`}><span className="dot" />{statusLabel(c.status)}</span></td>
                      <td className="muted" data-label="Filial / Hudud">{[c.branch_name, c.zone_name].filter(Boolean).join(" · ") || "Biriktirilmagan"}</td>
                      <td className="muted" data-label="Joylashuv">{c.location || "—"}</td>
                      <td className="muted" data-label="Oxirgi ko'rish" title={c.last_seen_at ? new Date(c.last_seen_at).toLocaleString("ru-RU") : ""}>{relTime(c.last_seen_at)}</td>
                      <td className="act">
                        <div className="row-actions">
                          <button className="btn-sq" style={{ width: 32, height: 32 }} title="Jonli ko'rish" onClick={() => setWatch(c)}><IcEye size={15} /><span className="bl">Ko'rish</span></button>
                          {c.source_type === "ezviz" && (
                            <button className="btn-sq" style={{ width: 32, height: 32 }} title="Oqim logi" onClick={() => openLog(c)}><IcFile size={15} /><span className="bl">Oqim logi</span></button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {shown.length === 0 && <tr><td colSpan={7}><div className="empty">Kamera topilmadi.</div></td></tr>}
                </tbody>
              </table>
            </div>
            <Pager page={page} size={PAGE} total={shown.length} onPage={goPage} />
          </>
        )}
      </div>

      {logFor && (
        <Modal title={`${logFor.name} — oqim logi`} wide onClose={() => setLogFor(null)}
          footer={<button className="btn ghost" onClick={() => openLog(logFor)}><IcRefresh size={15} />Yangilash</button>}>
          <pre className="log-pre">
            {logLines === null ? "Yuklanmoqda…" : (logLines.length ? logLines.join("\n") : "Log fayli yo'q — kamera hali ochilmagan.")}
          </pre>
        </Modal>
      )}

      {watch && <Player camera={watch} zoneName={watch.zone_name} onClose={() => setWatch(null)} />}
    </div>
  );
}
