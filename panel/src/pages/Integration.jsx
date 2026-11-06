import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Player from "../player/Player";
import { consumerFetch, getConsumerKey, setConsumerKey } from "../api/client";
import { statusColor, statusLabel } from "../lib/format";
import { PageHeader } from "../components/ui.jsx";
import {
  IcCamera, IcCameraOff, IcCheck, IcCopy, IcBranch, IcEye, IcZone, IcKey,
  IcPlug, IcPlus, IcPlus2, IcSearch, IcTrash,
} from "../components/icons.jsx";

// "Mening tizimim" — tashqi tizim o'ziga qo'shgan kameralar (faqat shu brauzerda, namuna uchun)
const MYKEY = "vz_consumer_cams";
const loadMine = () => { try { return JSON.parse(localStorage.getItem(MYKEY)) || []; } catch { return []; } };
const saveMine = (arr) => { try { localStorage.setItem(MYKEY, JSON.stringify(arr)); } catch { /* */ } };
const maskKey = (k) => (k && k.length > 14 ? `${k.slice(0, 10)}••••••••${k.slice(-4)}` : k || "—");

// Iste'molchi API orqali oxirgi kadr (X-API-Key bilan) — tashqi tizim aynan shuni oladi
function ConsumerSnapshot({ id, apiKey, active }) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!active || !apiKey) return;
    let alive = true, obj = null, timer = null;
    const load = async () => {
      let st = 0;
      try {
        const r = await fetch(`/api/v1/cameras/${id}/snapshot`, { headers: { "X-API-Key": apiKey } });
        st = r.status;
        if (alive && st === 200) {
          const b = await r.blob();
          if (obj) URL.revokeObjectURL(obj);
          obj = URL.createObjectURL(b); setUrl(obj);
        }
      } catch { /* tarmoq */ }
      if (alive && st === 202) timer = setTimeout(load, 5000);
    };
    load();
    return () => { alive = false; clearTimeout(timer); if (obj) URL.revokeObjectURL(obj); };
  }, [id, apiKey, active]);
  return url ? <img src={url} alt="" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} /> : null;
}

// Qadamlar chizig'i: bajarilgan — yashil belgi, joriy — urg'u
function Stepper({ connected, added }) {
  const steps = [
    ["Ulanish", connected ? "kalit qabul qilindi" : "API kalitni kiriting", connected],
    ["Kameralar", added ? `${added} ta tizimga qo'shilgan` : "ruxsat etilganlarni ko'ring", added > 0],
    ["Hujjatlar", "o'z tizimingizga ulang", false],
  ];
  const cur = connected ? (added ? 2 : 1) : 0;
  return (
    <div className="intg-stepper">
      {steps.map(([t, d, done], i) => (
        <div key={t} className={`st${done ? " done" : ""}${i === cur ? " cur" : ""}`}>
          <span className="n">{done ? <IcCheck size={14} /> : i + 1}</span>
          <div><b>{t}</b><span>{d}</span></div>
          {i < steps.length - 1 && <i className="bar" />}
        </div>
      ))}
    </div>
  );
}

// Ma'lumot oqimi sxemasi: tashqi tizim -> kalit -> server -> kameralar (harakatlanuvchi nuqtalar)
function FlowArt() {
  const nodes = [
    [40, "Tashqi tizim", <IcPlug size={18} key="a" />],
    [170, "X-API-Key", <IcKey size={18} key="b" />],
    [300, "Vizora", <IcCheck size={18} key="c" />],
    [430, "Kameralar", <IcCamera size={18} key="d" />],
  ];
  return (
    <svg className="intg-flow" viewBox="0 0 470 120" fill="none" aria-hidden>
      <defs>
        <linearGradient id="fl-line" x1="0" x2="1">
          <stop offset="0" stopColor="var(--accent)" stopOpacity=".15" />
          <stop offset=".5" stopColor="var(--accent)" stopOpacity=".7" />
          <stop offset="1" stopColor="var(--online)" stopOpacity=".5" />
        </linearGradient>
      </defs>
      <path d="M62 46 H408" stroke="url(#fl-line)" strokeWidth="2.5" strokeDasharray="6 6" className="fl-dash" />
      {[0, 1, 2].map((i) => <circle key={i} r="3.5" className="fl-dot" style={{ animationDelay: `${i * 0.9}s` }} />)}
      {nodes.map(([x, label, icon], i) => (
        <g key={label} transform={`translate(${x - 22} 24)`}>
          <rect width="44" height="44" rx="13" className={`fl-node${i === 2 ? " core" : ""}`} />
          <foreignObject x="13" y="13" width="18" height="18"><div className="fl-ic">{icon}</div></foreignObject>
          <text x="22" y="66" textAnchor="middle" className="fl-lbl">{label}</text>
        </g>
      ))}
    </svg>
  );
}

// Juda oddiy sintaksis ranglash (kalit so'zlar, satrlar, izohlar, sonlar) — kutubxonasiz
function highlight(src) {
  const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // `//` faqat URL'ning bir qismi bo'lmasa izoh (http:// ni ranglamaymiz)
  const re = /(#[^\n]*|(?<![:\w])\/\/[^\n]*)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|\b(const|await|new|if|return|import|from|curl|fetch|JSON|console)\b|\b(\d+)\b/g;
  let out = "", last = 0, m;
  while ((m = re.exec(src))) {
    out += esc(src.slice(last, m.index));
    const [tok, com, str, kw, num] = m;
    const cls = com ? "c" : str ? "s" : kw ? "k" : num ? "n" : "";
    out += `<span class="hl-${cls}">${esc(tok)}</span>`;
    last = m.index + tok.length;
  }
  return out + esc(src.slice(last));
}

export default function Integration() {
  const nav = useNavigate();
  const [key, setKey] = useState(getConsumerKey());
  const [me, setMe] = useState(null);
  const [tree, setTree] = useState([]);
  const [connecting, setConnecting] = useState(false);
  const [err, setErr] = useState(null);
  const [q, setQ] = useState("");
  const [onlyOnline, setOnlyOnline] = useState(false);
  const [picked, setPicked] = useState(() => new Set());
  const [mine, setMine] = useState(loadMine);
  const [watch, setWatch] = useState(null);

  const mineIds = useMemo(() => new Set(mine.map((c) => c.id)), [mine]);

  const connect = useCallback(async (k) => {
    const useKey = (k ?? key).trim();
    if (!useKey) { setErr("API kalitni kiriting"); return; }
    setConnecting(true); setErr(null);
    try {
      const info = await consumerFetch("/me", useKey);
      const t = await consumerFetch("/tree", useKey);
      setConsumerKey(useKey); setKey(useKey); setMe(info); setTree(t);
    } catch (e) { setMe(null); setTree([]); setErr(e.status === 401 ? "Kalit yaroqsiz yoki mijoz o'chirilgan" : e.message); }
    finally { setConnecting(false); }
  }, [key]);

  useEffect(() => { if (getConsumerKey()) connect(getConsumerKey()); /* eslint-disable-next-line */ }, []);

  function disconnect() { setConsumerKey(""); setKey(""); setMe(null); setTree([]); setErr(null); setPicked(new Set()); }
  const persist = (next) => { setMine(next); saveMine(next); };
  function addCam(cam) { if (!mineIds.has(cam.id)) persist([...mine, cam]); }
  function removeCam(id) { persist(mine.filter((c) => c.id !== id)); }

  // Daraxtni tekis ro'yxatga aylantiramiz
  const flat = useMemo(() => {
    const rows = [];
    for (const ent of tree) for (const f of ent.zones) for (const c of f.cameras)
      rows.push({ ...c, ent: ent.name, fac: f.name });
    return rows;
  }, [tree]);
  const onlineCount = flat.filter((c) => c.status === "online").length;
  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    return flat.filter((c) => (!onlyOnline || c.status === "online") &&
      (!n || [c.name, c.ent, c.fac].some((x) => (x || "").toLowerCase().includes(n))));
  }, [flat, q, onlyOnline]);

  const togglePick = (id) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  function addPicked() { persist([...mine, ...flat.filter((c) => picked.has(c.id) && !mineIds.has(c.id))]); setPicked(new Set()); }
  const allShownPicked = shown.length > 0 && shown.every((c) => picked.has(c.id) || mineIds.has(c.id));
  const pickAllShown = () => setPicked((s) => {
    const n = new Set(s);
    if (allShownPicked) shown.forEach((c) => n.delete(c.id)); else shown.forEach((c) => !mineIds.has(c.id) && n.add(c.id));
    return n;
  });

  // Ruxsat: daraxtdan nomlar bilan (mijoz faqat ruxsat etilganini ko'radi)
  const scopeChips = me?.all_access
    ? [{ k: "all", icon: <IcCamera size={12} />, t: "Barcha kameralar" }]
    : [
      ...(me?.scopes?.branches?.length ? [{ k: "e", icon: <IcBranch size={12} />, t: `${me.scopes.branches.length} filial` }] : []),
      ...(me?.scopes?.zones?.length ? [{ k: "f", icon: <IcZone size={12} />, t: `${me.scopes.zones.length} hudud` }] : []),
      ...(me?.scopes?.cameras?.length ? [{ k: "c", icon: <IcCamera size={12} />, t: `${me.scopes.cameras.length} kamera` }] : []),
    ];

  return (
    <div>
      <PageHeader
        eyebrow="Integratsiya"
        title="Tashqi tizim ko'rinishi"
        subtitle="Bu sahifa API mijoz nimani ko'rishini sinash uchun: kalit bilan ulaning, ruxsat etilgan kameralarni ko'ring va ochib tekshiring. Pastda — API hujjatlari."
        tag={null}
        rightSlot={<button className="btn ghost" onClick={() => nav("/clients")}><IcKey size={15} />API mijozlar</button>}
      />

      <Stepper connected={!!me} added={mine.length} />

      {/* 1-qadam: ulanish */}
      <div className={`card intg-step${me ? " on" : ""}`}>
        <div className="is-num">1</div>
        {me ? (
          <div className="is-body is-connected">
            <div className="ic-who">
              <div className="ic-av"><IcPlug size={22} /></div>
              <div style={{ minWidth: 0 }}>
                <div className="ic-name">{me.name} <span className="badge on"><span className="dot" />Ulandi</span></div>
                <div className="ic-chips">{scopeChips.map((x) => <span className="chip" key={x.k}>{x.icon}{x.t}</span>)}</div>
              </div>
            </div>
            <div className="ic-nums">
              <div><b>{me.camera_count}</b><span>kamera ko'ra oladi</span></div>
              <div><b className="on">{onlineCount}</b><span>hozir onlayn</span></div>
              <div><b>{mine.length}</b><span>tizimga qo'shilgan</span></div>
            </div>
            <div className="ic-key">
              <div className="label">API kalit</div>
              <div className="ic-keybox"><span className="mono">{maskKey(key)}</span><CopyIcon text={key} /></div>
              <div className="ic-note">Kalit faqat shu brauzerda saqlanadi. Umumiy kompyuterda tekshirib bo'lgach «Uzish»ni bosing.</div>
            </div>
            <button className="btn danger-soft ic-disc" onClick={disconnect}><IcPlug size={16} />Uzish</button>
          </div>
        ) : (
          <div className="is-body">
            <div className="is-title">API kalit bilan ulanish</div>
            <div className="is-sub">«API mijozlar» bo'limida kalit yarating, shu yerga joylashtiring — mijoz ko'radigan kameralar ochiladi.</div>
            <div className="ic-form">
              <div className="search-inline">
                <IcKey size={15} />
                <input className="input" value={key} placeholder="vz_…" spellCheck={false} autoComplete="off"
                  onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => e.key === "Enter" && connect()} />
              </div>
              <button className="btn" onClick={() => connect()} disabled={connecting}><IcPlug size={16} />{connecting ? "Ulanmoqda…" : "Ulanish"}</button>
            </div>
            <ul className="ic-points">
              <li><IcCheck size={13} />Faqat ruxsat etilgan filial / hudud / kameralar ko'rinadi</li>
              <li><IcCheck size={13} />Oqim havolalari vaqtinchalik token bilan</li>
              <li><IcCheck size={13} />Kalitni istalgan payt «API mijozlar»da yangilash yoki o'chirish mumkin</li>
            </ul>
          </div>
        )}
        {!me && <div className="is-art"><FlowArt /></div>}
        {err && <div className="error-banner" style={{ margin: "12px 0 0", gridColumn: "1 / -1" }}>{err}</div>}
      </div>

      {/* 2-qadam: kameralar */}
      {me && (
        <div className="rgrid intg-row" style={{ "--cols": "minmax(0,1.15fr) minmax(0,1fr)" }}>
          <div className="card clip">
            <div className="card-head wrap">
              <div>
                <div className="card-title"><span className="ci">2</span>Ruxsat etilgan kameralar <span className="count-chip plain">{flat.length}</span></div>
                <div className="card-sub ind">Mijoz API orqali aynan shularni oladi. Belgilab, «Mening tizimim»ga qo'shing.</div>
              </div>
            </div>
            <div className="ic-tools">
              <div className="search-inline">
                <IcSearch size={15} />
                <input className="input" placeholder="Kamera, filial yoki hudud…" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
              <label className="wall-check"><input type="checkbox" checked={onlyOnline} onChange={(e) => setOnlyOnline(e.target.checked)} />Faqat onlayn</label>
            </div>
            <div className="ic-list">
              <div className="ic-row head">
                <input type="checkbox" checked={allShownPicked} onChange={pickAllShown} title="Ko'rinayotganlarni belgilash" />
                <span>Kamera</span><span>Holat</span><span />
              </div>
              {shown.map((c) => (
                <div className={`ic-row${picked.has(c.id) ? " sel" : ""}`} key={c.id}>
                  <input type="checkbox" checked={picked.has(c.id)} disabled={mineIds.has(c.id)} onChange={() => togglePick(c.id)} />
                  <div className="ic-cam">
                    <span className="nm">{c.name}</span>
                    <span className="sub">{[c.ent, c.fac].filter(Boolean).join(" · ") || "Biriktirilmagan"}</span>
                  </div>
                  <span className="ic-st" style={{ color: statusColor(c.status) }}><i style={{ background: statusColor(c.status) }} />{statusLabel(c.status)}</span>
                  <div className="row-actions">
                    <button className="btn-sq" style={{ width: 30, height: 30 }} title="Ochib ko'rish (mijoz kaliti bilan)" onClick={() => setWatch(c)}><IcEye size={14} /></button>
                    {mineIds.has(c.id)
                      ? <button className="btn-sq" style={{ width: 30, height: 30, color: "var(--online)" }} title="Qo'shilgan — olib tashlash" onClick={() => removeCam(c.id)}><IcCheck size={14} /></button>
                      : <button className="btn-sq" style={{ width: 30, height: 30 }} title="Qo'shish" onClick={() => addCam(c)}><IcPlus2 size={14} /></button>}
                  </div>
                </div>
              ))}
              {shown.length === 0 && <div className="empty">Kamera topilmadi.</div>}
            </div>
            <div className="pager">
              <span className="range">{picked.size} ta tanlangan</span>
              <button className="btn sm" disabled={picked.size === 0} onClick={addPicked}><IcPlus size={15} />Tanlanganlarni qo'shish</button>
            </div>
          </div>

          <div className="card clip">
            <div className="card-head wrap">
              <div>
                <div className="card-title">Mening tizimim <span className="count-chip plain">{mine.length}</span></div>
                <div className="card-sub ind">Tashqi tizim ko'rinishi namunasi — kadrlar API orqali olinadi.</div>
              </div>
              {mine.length > 0 && <button className="link-btn" style={{ color: "var(--danger)" }} onClick={() => persist([])}><IcTrash size={14} />Tozalash</button>}
            </div>
            <div className="ic-mine">
              {mine.map((c) => (
                <div className="ic-tile" key={c.id}>
                  <button className="ic-shot" onClick={() => setWatch(c)} title="Ochib ko'rish">
                    <ConsumerSnapshot id={c.id} apiKey={key} active={c.status === "online"} />
                    {c.status !== "online" && <div className="ic-off"><IcCameraOff size={20} /></div>}
                    <span className="ic-pill"><i style={{ background: statusColor(c.status) }} />{statusLabel(c.status)}</span>
                  </button>
                  <div className="ic-tmeta">
                    <span className="nm" title={c.name}>{c.name}</span>
                    <button className="btn-sq danger" style={{ width: 24, height: 24 }} title="Olib tashlash" onClick={() => removeCam(c.id)}><IcTrash size={11} /></button>
                  </div>
                </div>
              ))}
              {mine.length === 0 && (
                <div className="ic-empty"><IcPlus size={20} /><b>Hali kamera qo'shilmagan</b><span>Chapdagi ro'yxatdan belgilab qo'shing</span></div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 3-qadam: hujjatlar — doim ko'rinadi */}
      <ApiDocs apiKey={me ? key : ""} />

      {watch && <Player camera={watch} zoneName={watch.fac || watch.zone_name} fetchStream={() => consumerFetch(`/cameras/${watch.id}/stream`)} onClose={() => setWatch(null)} />}
    </div>
  );
}

function CopyIcon({ text }) {
  const [done, setDone] = useState(false);
  return (
    <button className="btn-sq" style={{ width: 32, height: 32, flex: "none" }} title="Kalitni nusxalash"
      onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1400); }}>
      {done ? <IcCheck size={14} /> : <IcCopy size={14} />}
    </button>
  );
}

const ENDPOINTS = [
  ["GET", "/api/v1/me", "Mijoz nomi, ruxsat (scope) va ko'ra oladigan kameralar soni"],
  ["GET", "/api/v1/cameras", "Ruxsat etilgan kameralar · filtr: ?status=&branch_id=&zone_id=&q="],
  ["GET", "/api/v1/cameras/{id}", "Bitta kamera ma'lumoti"],
  ["GET", "/api/v1/cameras/{id}/stream", "Jonli oqim: HLS URL + vaqtinchalik token"],
  ["GET", "/api/v1/cameras/{id}/snapshot", "Oxirgi kadr (JPEG) · 200 rasm · 202 tayyorlanmoqda · 204 o'chiq"],
  ["GET", "/api/v1/tree", "Filial → hudud → kamera daraxti"],
  ["WS", "/ws/consumer?api_key=…", "Real vaqt: subscribe / unsubscribe / list, holat o'zgarishi push"],
];

function CopyBtn({ text }) {
  const [done, setDone] = useState(false);
  return <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1400); }}>{done ? <IcCheck size={14} /> : <IcCopy size={14} />}{done ? "Olindi" : "Nusxa"}</button>;
}

function ApiDocs({ apiKey }) {
  const [tab, setTab] = useState("endpoints");
  const origin = window.location.origin;
  const wsOrigin = origin.replace(/^http/, "ws");
  const K = apiKey?.trim() || "<API_KEY>";
  const code = {
    curl: `# Ruxsat etilgan kameralar\ncurl -H "X-API-Key: ${K}" \\\n  ${origin}/api/v1/cameras\n\n# Bitta kameraning jonli oqimi (HLS URL + token)\ncurl -H "X-API-Key: ${K}" \\\n  ${origin}/api/v1/cameras/<CAMERA_ID>/stream\n\n# Oxirgi kadr (JPEG)\ncurl -H "X-API-Key: ${K}" -o kadr.jpg \\\n  ${origin}/api/v1/cameras/<CAMERA_ID>/snapshot`,
    js: `const KEY = "${K}";\nconst base = "${origin}";\n\n// 1) Ruxsat etilgan kameralar\nconst cams = await fetch(base + "/api/v1/cameras", {\n  headers: { "X-API-Key": KEY },\n}).then((r) => r.json());\n\n// 2) Jonli oqim (HLS) URL\nconst { hls_url } = await fetch(\n  base + "/api/v1/cameras/" + cams[0].id + "/stream",\n  { headers: { "X-API-Key": KEY } }\n).then((r) => r.json());\n\n// 3) hls.js bilan ijro\nconst hls = new Hls();\nhls.loadSource(base + hls_url);\nhls.attachMedia(document.querySelector("video"));`,
    ws: `const ws = new WebSocket("${wsOrigin}/ws/consumer?api_key=${K}");\n\nws.onopen = () => ws.send(JSON.stringify({ type: "list" }));\n\nws.onmessage = (e) => {\n  const msg = JSON.parse(e.data);\n  if (msg.type === "cameras") console.log("kameralar:", msg.items);\n  // subscribe javobi: HLS URL + vaqtinchalik token\n  if (msg.type === "stream_ready") console.log("oqim:", msg.hls_url, msg.token);\n  // obuna bo'lingan kamera holati o'zgarganda (real vaqt)\n  if (msg.type === "status_changed") console.log(msg.camera_id, "→", msg.status);\n  if (msg.type === "error") console.warn(msg.reason);\n};\n\n// Kameraga obuna (ulanish ochilgandan keyin)\nws.send(JSON.stringify({ type: "subscribe", camera_id: "<CAMERA_ID>" }));`,
  };
  const TABS = [["endpoints", "Endpointlar"], ["curl", "cURL"], ["js", "JavaScript"], ["ws", "WebSocket"]];
  return (
    <div className="card clip intg-docs">
      <div className="card-head wrap">
        <div>
          <div className="card-title"><span className="ci">{apiKey ? 3 : 2}</span>API hujjatlari</div>
          <div className="card-sub ind">
            Har so'rovda <span className="chip">X-API-Key</span> sarlavhasi. Oqim URL'lari vaqtinchalik token bilan himoyalangan.
            {!apiKey && " Ulansangiz, namunalarga kalitingiz o'zi qo'yiladi."}
          </div>
        </div>
      </div>
      <div className="list-tabs" role="tablist">
        {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={`lt${tab === k ? " on" : ""}`} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "endpoints" ? (
        <div className="ic-eps">
          {ENDPOINTS.map(([m, p, d]) => (
            <div className="ic-ep" key={p}>
              <span className={`ic-m ${m === "WS" ? "ws" : ""}`}>{m}</span>
              <code>{p}</code>
              <span className="d">{d}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="ic-code">
          <div className="ic-editor">
            <div className="ic-ed-bar">
              <span className="dots"><i /><i /><i /></span>
              <span className="fn">{{ curl: "terminal.sh", js: "integratsiya.js", ws: "realtime.js" }[tab]}</span>
              <CopyBtn text={code[tab]} />
            </div>
            <pre className="ic-pre"><code dangerouslySetInnerHTML={{ __html: highlight(code[tab]) }} /></pre>
          </div>
        </div>
      )}
    </div>
  );
}
