import { useEffect, useMemo, useRef, useState } from "react";
import { IcChart } from "./icons.jsx";

// Onlayn kameralar tarixi — ustunli grafik (Dashboard).
//  * Har bo'lak bitta ustun (gradient), o'rtacha qiymat punktir chiziq.
//  * Hover: ustun yorqinlashadi, tepasida "vaqt · N ta" yorlig'i.
//  * `prev` berilsa (o'tgan davr, bir xil uzunlikda) — punktir egri bilan solishtiriladi.
//  * SVG haqiqiy piksel o'lchamida chiziladi (ResizeObserver) — hech narsa cho'zilmaydi.

const UZ_MON = ["yan", "fev", "mar", "apr", "may", "iyn", "iyl", "avg", "sen", "okt", "noy", "dek"];
const pad = (n) => String(n).padStart(2, "0");
const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const dm = (d) => `${pad(d.getDate())}.${pad(d.getMonth() + 1)}`;
const WEEK = ["Yak", "Dush", "Sesh", "Chor", "Pay", "Jum", "Shan"];
export const fmtWhen = (t) => { const d = new Date(t * 1000); return `${d.getDate()}-${UZ_MON[d.getMonth()]}, ${hm(d)}`; };

// O'q uchun "chiroyli" yuqori chegara va qadam (1, 2, 5 × 10^k)
function niceMax(hi, ticks = 4) {
  const raw = Math.max(hi, 1) / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  return { max: step * ticks, step };
}

function smooth(pts) {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    d += ` C${p1[0] + (p2[0] - p0[0]) / 6},${p1[1] + (p2[1] - p0[1]) / 6} ${p2[0] - (p3[0] - p1[0]) / 6},${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]},${p2[1]}`;
  }
  return d;
}

// `dep`: konteyner keyinroq paydo bo'lsa (avval bo'sh holat chizilgan) qayta ulanadi
function useSize(dep) {
  const ref = useRef(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, [dep]);
  return [ref, size];
}

export function chartStats(points) {
  const known = points.filter((p) => p.online != null);
  if (!known.length) return null;
  const vals = known.map((p) => p.online);
  return {
    now: known[known.length - 1].online,
    avg: Math.round((vals.reduce((a, v) => a + v, 0) / vals.length) * 10) / 10,
    min: Math.min(...vals), max: Math.max(...vals),
  };
}

// Namunalar orasidagi qisqa bo'shliq (<= 10 daq) oxirgi qiymat bilan to'ldiriladi: kamera soni
// namunalar orasida o'zgarmay turadi. Uzunroq bo'shliq (server o'chgan) — bo'sh qoladi.
function holdGaps(points) {
  const step = points.length > 1 ? points[1].t - points[0].t : 60;
  const GAP = 600;
  if (step >= GAP) return points;
  let last = null;
  return points.map((p) => {
    if (p.online != null) { last = p; return p; }
    if (last && p.t - last.t <= GAP) return { ...p, online: last.online, total: last.total, held: true };
    return p;
  });
}

export default function ActivityChart({ points: raw, prev, partial, since }) {
  const points = useMemo(() => holdGaps(raw), [raw]);
  const [hover, setHover] = useState(null);
  const hasData = points.some((p) => p.online != null);
  const [ref, { w, h }] = useSize(hasData);
  const L = 34, R = 8, T = 30, B = 26;           // T: hover yorlig'i uchun joy

  const geo = useMemo(() => {
    const n = points.length;
    if (!n || w < 60 || h < 60) return null;
    const vals = points.map((p) => p.online).filter((v) => v != null);
    const pvals = (prev || []).map((p) => p.online).filter((v) => v != null);
    const { max, step } = niceMax(Math.max(...vals, ...pvals, 1));
    const plotW = w - L - R, plotH = h - T - B;
    const slot = plotW / n;
    const bw = Math.max(2, Math.min(28, slot * 0.68));
    const cx = (i) => L + slot * (i + 0.5);
    const Y = (v) => T + (1 - v / max) * plotH;
    const ticks = Array.from({ length: max / step + 1 }, (_, k) => k * step);
    const span = n > 1 ? points[n - 1].t - points[0].t : 0;
    const daily = n > 1 && points[1].t - points[0].t >= 86400;
    const fmt = (t) => { const d = new Date(t * 1000); return daily ? dm(d) : span < 2 * 86400 ? hm(d) : dm(d); };
    const fmtTip = (t) => { const d = new Date(t * 1000); return daily ? `${WEEK[d.getDay()]}, ${dm(d)}` : fmt(t); };
    const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(plotW / 70))));
    const avg = vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : null;
    const prevLine = prev && prev.length === n
      ? smooth(prev.map((p, i) => (p.online == null ? null : [cx(i), Y(p.online)])).filter(Boolean))
      : "";
    return { max, cx, Y, bw, slot, ticks, fmt, fmtTip, labelEvery, avg, prevLine, base: T + plotH };
  }, [points, prev, w, h]);

  if (!hasData) {
    return (
      <div className="ac-empty">
        <IcChart size={22} />
        <div>Tarix yig'ilmoqda</div>
        <span>Har daqiqada bitta nuqta qo'shiladi — birinchisi bir daqiqada paydo bo'ladi.</span>
      </div>
    );
  }

  const onMove = (e) => {
    if (!geo) return;
    const x = e.clientX - e.currentTarget.getBoundingClientRect().left;
    const i = Math.floor((x - L) / geo.slot);
    setHover(i >= 0 && i < points.length ? i : null);
  };
  const hp = hover != null ? points[hover] : null;
  const last = points.reduce((a, p, i) => (p.online != null ? i : a), -1);

  return (
    <div className="ac">
      <div className="ac-plot" ref={ref} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {geo && (
          <svg width={w} height={h}>
            <defs>
              <linearGradient id="ac-bar" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.9" />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.18" />
              </linearGradient>
              <linearGradient id="ac-bar-on" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity="1" />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.55" />
              </linearGradient>
            </defs>
            {geo.ticks.map((v) => (
              <g key={v}>
                <line x1={L} x2={w - R} y1={geo.Y(v)} y2={geo.Y(v)} className="ac-grid" />
                <text x={L - 8} y={geo.Y(v) + 4} className="ac-yl">{v}</text>
              </g>
            ))}
            {/* Ma'lumotsiz bo'lak — xira "bo'sh" ustun: o'q to'liq ko'rinadi, bo'shliq aniq */}
            {points.map((p, i) => p.online == null && (
              <rect key={`e${i}`} x={geo.cx(i) - geo.bw / 2} y={T} width={geo.bw} height={geo.base - T}
                rx={Math.min(3, geo.bw / 3)} className={`ac-empty-bar${i === hover ? " on" : ""}`} />
            ))}
            {points.map((p, i) => p.online != null && p.online > 0 && (
              <rect key={i}
                x={geo.cx(i) - geo.bw / 2} y={geo.Y(p.online)}
                width={geo.bw} height={Math.max(1, geo.base - geo.Y(p.online))}
                rx={Math.min(3, geo.bw / 3)}
                fill={i === hover ? "url(#ac-bar-on)" : "url(#ac-bar)"}
                className={`ac-bar${hover != null && i !== hover ? " dim" : ""}${i === last ? " last" : ""}`} />
            ))}
            {geo.prevLine && <path d={geo.prevLine} className="ac-prev" />}
            {geo.avg != null && (
              <line x1={L} x2={w - R} y1={geo.Y(geo.avg)} y2={geo.Y(geo.avg)} className="ac-avg" />
            )}
            {points.map((p, i) => i % geo.labelEvery === 0 && (
              <text key={i} x={geo.cx(i)} y={h - 6} className="ac-xl" textAnchor="middle">{geo.fmt(p.t)}</text>
            ))}
          </svg>
        )}
        {geo && hp && (
          <div className={`ac-tip${hp.online == null ? " none" : ""}`}
            style={{ left: Math.min(Math.max(geo.cx(hover), L + 60), w - 70), top: hp.online == null ? geo.base - 20 : geo.Y(hp.online) }}>
            {geo.fmtTip(hp.t)} · {hp.online == null ? "ma'lumot yo'q" : <b>{hp.online} ta</b>}
          </div>
        )}
      </div>
      {partial && since && <div className="ac-note">Tarix {fmtWhen(since)} dan beri yozilmoqda</div>}
    </div>
  );
}
