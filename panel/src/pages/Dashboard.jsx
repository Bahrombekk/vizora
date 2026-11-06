import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useList } from "../lib/useList";
import { relTime, statusColor, statusLabel } from "../lib/format";
import { PageHeader, Kpi } from "../components/ui.jsx";
import Player from "../player/Player";
import CamSnapshot from "../player/CamSnapshot";
import ActivityChart, { chartStats } from "../components/ActivityChart";
import {
  IcActivity, IcArrowRight, IcBolt, IcCamera, IcCameraOff, IcChart, IcCloud, IcDatabase, IcBranch,
  IcZone, IcGrid, IcPlay, IcPlug, IcPlus, IcRefresh, IcServer, IcShield, IcTrend, IcWarn,
} from "../components/icons.jsx";

const RANGES = [["24h", "24 soat"], ["7d", "7 kun"], ["30d", "30 kun"]];
const RANGE_TEXT = { "24h": "So'nggi 24 soat", "7d": "So'nggi 7 kun", "30d": "So'nggi 30 kun" };
// Hodisa matni: probe yozgan haqiqiy holat o'zgarishi.
const EVENT_TEXT = {
  online: "Onlayn bo'ldi", offline: "Aloqa uzildi", error: "Xatolik aniqlandi", no_stream: "Oqim kelmayapti",
};

// Kameralar kartasi: karta kengligiga qarab nechta ustun sig'ishini o'lchaydi,
// shunda plitkalar har doim 2 to'liq qatorni egallaydi (keng ekranda bo'sh joy qolmaydi).
const TILE_MIN = 210, TILE_GAP = 14, TILE_ROWS = 2, TILE_MAX = 16;
function useTileCount() {
  const ref = useRef(null);
  const [cols, setCols] = useState(4);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const w = e.contentRect.width;
      setCols(Math.max(2, Math.floor((w + TILE_GAP) / (TILE_MIN + TILE_GAP))));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, cols];
}

export default function Dashboard() {
  const nav = useNavigate();
  const fac = useList("/zones");
  const cams = useList(`/cameras?status=online&limit=${TILE_MAX}`, { pollMs: 60000 });
  const [gridRef, cols] = useTileCount();
  const [watch, setWatch] = useState(null);
  const [sum, setSum] = useState(null);
  const [health, setHealth] = useState(null);
  const [events, setEvents] = useState([]);
  const [rng, setRng] = useState("24h");
  const [hist, setHist] = useState(null);
  const facById = useMemo(() => Object.fromEntries(fac.data.map((f) => [f.id, f])), [fac.data]);

  useEffect(() => {
    const load = () => {
      apiFetch("/dashboard/summary").then(setSum).catch(() => {});
      apiFetch("/dashboard/health").then(setHealth).catch(() => setHealth(null));
      apiFetch("/dashboard/events?limit=8").then((d) => setEvents(d || [])).catch(() => {});
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let stop = false;
    const load = () => apiFetch(`/dashboard/history?range=${rng}&tz=${-new Date().getTimezoneOffset()}`).then((d) => { if (!stop) setHist(d); }).catch(() => {});
    load();
    const id = setInterval(load, 15000);
    return () => { stop = true; clearInterval(id); };
  }, [rng]);

  const s = sum || {};
  const total = s.cameras || 0;
  const pct = (v) => (total ? Math.round(((v || 0) / total) * 100) : 0);
  const sources = [s.ezviz ? `${s.ezviz} EZVIZ` : null, s.rtsp ? `${s.rtsp} RTSP` : null].filter(Boolean).join(" · ");

  const kpis = [
    { icon: <IcBranch size={20} />, iconClass: "info", label: "Filiallar", value: s.branches ?? "—", foot: "Ro'yxatdagi filiallar", go: "/branches" },
    { icon: <IcZone size={20} />, iconClass: "info", label: "Hududlar", value: s.zones ?? "—", foot: "Ro'yxatdagi hududlar", go: "/zones" },
    { icon: <IcCamera size={20} />, label: "Kameralar", value: s.cameras ?? "—", foot: sources || "Jami kameralar", go: "/cameras" },
    { icon: <IcActivity size={20} />, iconClass: "ok", label: "Onlayn", value: s.online ?? "—", foot: "Faol kameralar", donut: pct(s.online), donutColor: "var(--online)", go: "/monitoring" },
    { icon: <IcCameraOff size={20} />, iconClass: "off", label: "Oflayn", value: s.offline ?? "—", foot: "Aloqasiz", donut: pct(s.offline), donutColor: "var(--danger)", go: "/monitoring" },
    // ALOHIDA KATEGORIYA: qurilma javob beradi-yu, video bermaydi.
    { icon: <IcWarn size={20} />, iconClass: "err", label: "Oqim yo'q", value: s.no_stream ?? "—", foot: "Onlayn, lekin video kelmaydi", go: "/monitoring" },
  ];

  const h = health || {};
  const services = [
    { n: "Video server (MediaMTX)", ok: h.mediamtx, icon: <IcServer size={16} /> },
    { n: "Ma'lumotlar bazasi", ok: h.database, icon: <IcDatabase size={16} /> },
    { n: "Redis (hodisalar, kesh)", ok: h.redis, icon: <IcBolt size={16} /> },
    { n: "Holat tekshiruvi", ok: h.probe, icon: <IcRefresh size={16} />, note: h.probe_age != null ? `${h.probe_age} s oldin` : null },
    { n: "EZVIZ hisoblar", ok: h.ezviz_accounts > 0 ? true : null, icon: <IcCloud size={16} />, okText: `${h.ezviz_accounts ?? 0} ta hisob`, nullText: "Sozlanmagan" },
  ];
  const bad = health ? services.filter((x) => x.ok === false).length : 0;
  const healthText = !health ? "Tekshirilmoqda…" : bad ? `${bad} ta xizmatda muammo` : "Barcha xizmatlar normal ishlayapti";

  const cs = chartStats(hist?.points || []);

  const actions = [
    { t: "Yangi kamera", s: "Kamera qo'shish", icon: <IcPlus size={18} />, go: "/cameras" },
    { t: "Jonli devor", s: "Ko'p kamerani birga ko'rish", icon: <IcGrid size={18} />, go: "/wall" },
    { t: "Yangi filial", s: "Filial yaratish", icon: <IcBranch size={18} />, go: "/branches" },
    { t: "Integratsiya", s: "API ulash", icon: <IcPlug size={18} />, go: "/integration" },
  ];

  return (
    <div>
      <PageHeader
        eyebrow="Boshqaruv paneli"
        title="Tizim holati va umumiy statistika"
        subtitle="Barcha kameralar, filiallar va hududlar faoliyatini real vaqtda kuzatib boring."
        tag={null}
        rightSlot={
          <div className={`sys-pill ${!health ? "" : bad ? "bad" : "ok"}`}>
            <span className="dot" />{healthText}
          </div>
        }
      />

      <div className="kpi-grid dash-kpis">
        {kpis.map(({ go, ...k }) => (
          <div key={k.label} className="kpi-link" role="link" tabIndex={0} onClick={() => nav(go)}
            onKeyDown={(e) => e.key === "Enter" && nav(go)}>
            <Kpi {...k} />
          </div>
        ))}
      </div>

      <div className="dash-grid">
        {/* Faollik grafigi */}
        <div className="card dash-chart">
          <div className="card-head wrap ac-head">
            <div>
              <div className="card-title"><span className="ci"><IcChart size={16} /></span>Kamera faolligi</div>
              <div className="card-sub ind">
                {RANGE_TEXT[rng]}{cs && <> · o'rtacha <b>{cs.avg}</b> · eng kam <b>{cs.min}</b> · eng ko'p <b>{cs.max}</b></>}
              </div>
            </div>
            <div className="ac-right">
              <div className="seg">
                {RANGES.map(([k, l]) => (
                  <button key={k} className={rng === k ? "on" : ""} onClick={() => setRng(k)}>{l}</button>
                ))}
              </div>
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

        {/* Tizim holati */}
        <div className="card pad dash-health">
          <div className="card-title" style={{ marginBottom: 4 }}>
            <span className="ci" style={bad ? { background: "var(--errorBg)", color: "var(--error)" } : { background: "var(--onlineBg)", color: "var(--online)" }}><IcShield size={16} /></span>
            Tizim holati
          </div>
          <div className="card-sub ind" style={{ marginBottom: 10 }}>{healthText}</div>
          {services.map((sv) => {
            const st = sv.ok === true ? "ok" : sv.ok === false ? "bad" : "na";
            return (
              <div className={`svc ${st}`} key={sv.n}>
                <span className="si">{sv.icon}</span>
                <span className="sn">{sv.n}{sv.note && <em>{sv.note}</em>}</span>
                <span className="ss">{!health ? "…" : st === "ok" ? (sv.okText || "Ishlayapti") : st === "bad" ? "Javob yo'q" : sv.nullText}</span>
              </div>
            );
          })}
        </div>

        {/* So'nggi hodisalar */}
        <div className="card clip dash-events">
          <div className="card-head">
            <div className="card-title"><span className="ci"><IcActivity size={16} /></span>So'nggi hodisalar</div>
            <button className="link-btn" onClick={() => nav("/monitoring")}>Monitoring <IcArrowRight size={15} /></button>
          </div>
          <div>
            {events.map((ev, i) => (
              <div key={`${ev.camera_id}-${ev.ts}-${i}`} className="evt">
                <span className="ed" style={{ background: statusColor(ev.status) }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="et ell">{ev.name}</div>
                  <div className="es" style={{ color: statusColor(ev.status) }}>{EVENT_TEXT[ev.status] || statusLabel(ev.status)}</div>
                </div>
                <span className="ex" title={new Date(ev.ts * 1000).toLocaleString("ru-RU")}>{relTime(new Date(ev.ts * 1000).toISOString())}</span>
              </div>
            ))}
            {events.length === 0 && (
              <div className="empty">Hozircha hodisa yo'q.<br /><span style={{ fontSize: 12 }}>Kamera holati o'zgarganda shu yerda paydo bo'ladi.</span></div>
            )}
          </div>
        </div>

        {/* Onlayn kameralar — haqiqiy oxirgi kadr */}
        <div className="card clip dash-cams">
          <div className="card-head">
            <div>
              <div className="card-title">
                <span className="ci"><IcCamera size={16} /></span>Onlayn kameralar
                {s.online != null && <span className="count-chip">{s.online}</span>}
              </div>
              <div className="card-sub ind">Oxirgi kadr · bosing — jonli ochiladi</div>
            </div>
            <div className="dc-links">
              <button className="link-btn" onClick={() => nav("/cameras")}>Barchasi</button>
              <button className="link-btn" onClick={() => nav("/wall")}>Jonli devor <IcArrowRight size={15} /></button>
            </div>
          </div>
          <div className="dash-cam-grid" ref={gridRef} style={{ "--cols": cols }}>
            {cams.data.slice(0, cols * TILE_ROWS).map((c) => (
              <button key={c.id} className="dash-cam" onClick={() => setWatch(c)} title={c.name}>
                <div className="cam-feed">
                  <div className="dc-ph"><IcCamera size={22} /></div>
                  <CamSnapshot id={c.id} active />
                  <div className="stat-pill">
                    <span className="dot" style={{ background: statusColor(c.status) }} />
                    <span className="t">{statusLabel(c.status)}</span>
                  </div>
                  <span className="dc-src">{c.source_type === "ezviz" ? "EZVIZ" : "RTSP"}</span>
                  <div className="dc-meta">
                    <div className="dc-name ell">{c.name}</div>
                    {facById[c.zone_id]?.name && <div className="dc-sub ell">{facById[c.zone_id].name}</div>}
                  </div>
                  <div className="play-hover"><span className="circle"><IcPlay size={20} /></span></div>
                </div>
              </button>
            ))}
            {!cams.loading && cams.data.length === 0 && <div className="empty" style={{ gridColumn: "1/-1" }}>Onlayn kamera yo'q.</div>}
          </div>
        </div>

        {/* Tezkor amallar */}
        <div className="card pad dash-actions">
          <div className="card-title" style={{ marginBottom: 14 }}><span className="ci"><IcTrend size={16} /></span>Tezkor amallar</div>
          <div className="qa-grid">
            {actions.map((a) => (
              <button key={a.t} className="qa" onClick={() => nav(a.go)}>
                <span className="qa-ico">{a.icon}</span>
                <div>
                  <div className="qa-t">{a.t}</div>
                  <div className="qa-s">{a.s}</div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {watch && <Player camera={watch} zoneName={facById[watch.zone_id]?.name} onClose={() => setWatch(null)} />}
    </div>
  );
}

