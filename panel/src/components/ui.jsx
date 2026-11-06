import { useEffect, useState } from "react";

// ===== Soat / sana =====
export function useClock(intervalMs = 1000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

const UZ_MONTHS = ["yan", "fev", "mar", "apr", "may", "iyn", "iyl", "avg", "sen", "okt", "noy", "dek"];
const UZ_WEEKDAYS = ["Yakshanba", "Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba"];
const pad = (n) => String(n).padStart(2, "0");

export function uzDate(d = new Date()) {
  return {
    date: `${d.getDate()}-${UZ_MONTHS[d.getMonth()]}, ${d.getFullYear()}`,
    weekday: UZ_WEEKDAYS[d.getDay()],
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
    clock: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
  };
}

// ===== Nuqtali O'zbekiston xaritasi (bezak) =====
const UZ_PATH =
  "M12,64 C40,44 70,50 96,44 C120,39 150,58 180,54 C210,50 232,40 262,54 C290,66 300,58 330,66 " +
  "C356,73 372,58 400,66 C388,80 396,92 380,102 C398,112 386,132 360,128 C356,146 330,150 316,138 " +
  "C300,150 276,144 258,150 C238,156 224,146 200,152 C176,158 160,176 138,166 C118,158 96,166 76,156 " +
  "C56,146 40,150 30,132 C20,116 6,96 12,64 Z";
const UZ_CITIES = [
  [44, 74], [92, 96], [150, 92], [196, 116], [246, 104], [300, 92], [340, 78], [378, 92], [232, 138],
];

export function UzMap({ width = 380, height = 190, uid = "uz", style }) {
  const dotsId = `dots-${uid}`;
  const clipId = `clip-${uid}`;
  return (
    <svg className="uzmap" width={width} height={height} viewBox="0 0 420 210" fill="none" style={style} aria-hidden>
      <defs>
        <pattern id={dotsId} width="7.5" height="7.5" patternUnits="userSpaceOnUse">
          <circle cx="1.6" cy="1.6" r="1.05" className="dot" />
        </pattern>
        <clipPath id={clipId}><path d={UZ_PATH} /></clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>
        <rect width="420" height="210" fill={`url(#${dotsId})`} />
      </g>
      {UZ_CITIES.map(([x, y], i) => (
        <g key={i}>
          <circle cx={x} cy={y} r="7" className="cityglow" style={{ animation: `floatglow ${2.6 + (i % 4) * 0.5}s ease-in-out infinite` }} />
          <circle cx={x} cy={y} r="2.4" className="city" />
        </g>
      ))}
    </svg>
  );
}

// ===== Sahifa sarlavhasi =====
export function PageHeader({ eyebrow, title, subtitle, tag = ["Xavfsiz muhit.", "Barqaror kelajak."], crumbs, badge, mapUid = "hdr", rightSlot }) {
  return (
    <div className={`page-head${badge ? " with-badge" : ""}`}>
      <div className="ph-main">
        {crumbs && (
          <div className="crumbs">
            {crumbs.map((c, i) => (
              <span key={i} style={{ display: "contents" }}>
                {i > 0 && <span className="sep">›</span>}
                {i === crumbs.length - 1 ? <b>{c}</b> : <span>{c}</span>}
              </span>
            ))}
          </div>
        )}
        <div className="ph-lead">
          {badge && <div className="ph-badge">{badge}</div>}
          <div>
            {eyebrow && <div className="eyebrow">{eyebrow}</div>}
            <h1>{title}</h1>
          </div>
        </div>
        {subtitle && <p className="ph-sub">{subtitle}</p>}
      </div>
      <div className="ph-right">
        {rightSlot || <UzMap uid={mapUid} />}
        {tag && (
          <div className="ph-tag">
            {Array.isArray(tag) ? tag.map((t, i) => <div key={i}>{t}</div>) : tag}
            <div className="accentline" />
          </div>
        )}
      </div>
    </div>
  );
}

// ===== Donut halqa =====
export function Donut({ value = 0, size = 62, stroke = 7, color = "var(--accent)", track = "rgba(127,140,160,.16)", label, sub }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="donut" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
          strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)} strokeLinecap="round"
          style={{ transition: "stroke-dashoffset .7s ease" }} />
      </svg>
      <div className="donut-lbl" style={{ color, flexDirection: "column", lineHeight: 1 }}>
        <span>{label ?? `${Math.round(pct)}%`}</span>
        {sub && <span style={{ fontSize: 8.5, color: "var(--faint)", fontWeight: 500, marginTop: 2 }}>{sub}</span>}
      </div>
    </div>
  );
}

// ===== KPI karta =====
export function Kpi({ icon, iconClass = "", label, value, delta, deltaClass = "up", foot, donut, donutColor, ring }) {
  return (
    <div className="kpi">
      <div className="kpi-top">
        <div className={`kpi-ico ${iconClass}`}>{icon}</div>
        <span className="kpi-lbl">{label}</span>
      </div>
      <div className="kpi-body">
        <div>
          <div className="kpi-val">{value}</div>
          {(delta || foot) && (
            <div className="kpi-foot">
              {delta && <span className={`kpi-delta ${deltaClass}`}>{delta}</span>}
              {delta && foot ? " · " : ""}{foot}
            </div>
          )}
        </div>
        {donut != null && <Donut value={donut} color={donutColor || "var(--accent)"} size={58} stroke={6} />}
        {ring}
      </div>
    </div>
  );
}

// ===== Mini maydon grafigi (SVG) =====
export function MiniArea({ data = [], width = 640, height = 150, color = "var(--accent)", fillId = " area", showAxis = false, bars = false }) {
  if (!data.length) data = [0];
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const pad = 6;
  const w = width, h = height;
  const range = max - min || 1;
  const x = (i) => (data.length === 1 ? w / 2 : (i / (data.length - 1)) * (w - pad * 2) + pad);
  const y = (v) => h - pad - ((v - min) / range) * (h - pad * 2);
  const line = data.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(data.length - 1).toFixed(1)},${h} L${x(0).toFixed(1)},${h} Z`;
  const gid = `mg-${fillId}`;
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block" }}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {bars
        ? data.map((v, i) => {
            const bw = (w - pad * 2) / data.length * 0.62;
            const bx = x(i) - bw / 2;
            const by = y(v);
            return <rect key={i} x={bx} y={by} width={bw} height={h - pad - by} rx={Math.min(3, bw / 2)} fill={color} opacity="0.75" />;
          })
        : (<>
            <path d={area} fill={`url(#${gid})`} />
            <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          </>)}
    </svg>
  );
}

// ===== Pastki banner =====
import { IcLogo } from "./icons.jsx";
export function Banner({ title = "Vizora", sub, tag = "Har bir manzil nazoratda" }) {
  return (
    <div className="banner">
      <div className="b-ico"><IcLogo size={22} /></div>
      <div>
        <div className="b-title">{title}</div>
        {sub && <div className="b-sub">{sub}</div>}
      </div>
      <div className="b-tag">{tag}<span className="line" /></div>
    </div>
  );
}

// ===== Holat belgisi =====
export function StatusBadge({ status, labels = { online: "Onlayn", offline: "Oflayn", error: "Xatolik" } }) {
  const cls = status === "online" ? "on" : status === "error" ? "err" : "off";
  return <span className={`badge ${cls}`}><span className="dot" />{labels[status] || status}</span>;
}
