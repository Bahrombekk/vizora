import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, apiList } from "../api/client";
import { connectWs, hevcSupported, wsSupported } from "../player/wsplayer.js";
import { useList } from "../lib/useList";
import { PageHeader } from "../components/ui.jsx";
import Player from "../player/Player";
import {
  IcCamera, IcCameraOff, IcChevronLeft, IcChevronRight, IcExpand, IcPause, IcPlay, IcSearch, IcShrink,
} from "../components/icons.jsx";

// ===== Jonli devor =====
// O'lchandi (2026-09-11, RTX 5090 + shu brauzer): TO'LIQ o'lchamli oqimda 12 ta
// plitka toza, 24 tada brauzer dekoderi yiqilardi (3200x1800 kameralar). Endi
// plitka HAR DOIM substream oladi (640x360, ~0.11 Mbit/s) — brauzer uchun bu
// yuk emas, shuning uchun "dekoder ulgurmadi" degan AVTO-CHEGARA OLIB TASHLANDI:
// u sog'lom kamerani ham 60s snapshot'ga tushirar va u qayta tiklanmasdi.
// Qolgan ikki qoida:
//   1) ekrandan chiqqan plitka oqimni TO'XTATADI (IntersectionObserver);
//   2) oqim uzilsa yoki qotib qolsa plitka O'ZI qayta ulanadi (watchdog).
const GRIDS = [
  { n: 2, label: "2×2" }, { n: 3, label: "3×3" }, { n: 4, label: "4×4" },
  { n: 5, label: "5×5" }, { n: 6, label: "6×6" },
];
// Devorda nechta kamera turadi (hammasi jonli). Bu sonda 6x6 to'r bilan
// birgalikda foydalanuvchi devorining hajmi aniqlanadi.
const COUNTS = [4, 9, 16, 25, 36];
const gridFor = (n) => Math.ceil(Math.sqrt(n));
const SNAP_REFRESH_MS = 15000;
// Jonli plitkadan shuncha vaqt kadr kelmasa — qotgan deb hisoblab qayta ulanamiz.
const STALL_MS = 8000;
// Oqim uzilgach qayta ulanishdan oldingi kutish (bulutni bezovta qilmaslik).
const RETRY_MS = 3000;
// Plitka birinchi kadrni shuncha kutadi (bulut sovuq starti ~6-9s). Undan keyin
// snapshot'ga tushadi — devor tez to'ladi va muammoli kamera ko'rinib qoladi.
const TILE_WAIT_MS = 20000;
// Ulanmagan kamerani shuncha vaqt qayta urinmaymiz (bulutni bekorga urmaslik).
const FAIL_TTL_MS = 45000;

/** Bitta plitka: jonli bo'lsa WS+WebCodecs canvas, aks holda snapshot. */
function Tile({ cam, idx = 0, wantLive, onLiveChange, onStats, onVisible, onFail, onOpen, place, sub = true }) {
  const canvasRef = useRef(null);
  const boxRef = useRef(null);
  const [state, setState] = useState("idle");   // idle | ulanish | live | xato
  const [codec, setCodec] = useState("");
  const [snap, setSnap] = useState(null);
  const [visible, setVisible] = useState(false);
  const [gen, setGen] = useState(0);        // qayta ulanish hisoblagichi
  const beatRef = useRef(0);                // oxirgi kadr pulsi (watchdog uchun)

  // Ekranda ko'rinadimi — ko'rinmasa oqim ushlab turilmaydi (resurs tejash).
  // Natija OTA komponentga ham beriladi: jonli oqim cheklovi RO'YXATNING
  // birinchi N tasiga emas, KO'RINADIGAN plitkalarga qo'llanishi kerak, aks holda
  // pastga skroll qilganda yangi plitkalar jonli bo'lmaydi.
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setVisible(true); onVisible?.(cam.id, true);
      return () => onVisible?.(cam.id, false);
    }
    const io = new IntersectionObserver(([e]) => {
      setVisible(e.isIntersecting);
      onVisible?.(cam.id, e.isIntersecting);
    }, { rootMargin: "100px" });
    io.observe(el);
    return () => { io.disconnect(); onVisible?.(cam.id, false); };
  }, [cam.id, onVisible]);

  // Snapshot (poster) — jonli kelguncha va jonli bo'lmaganda ko'rinadi.
  useEffect(() => {
    if (!visible || cam.status !== "online") return;
    let alive = true, url = null, timer = null;
    const load = async () => {
      try {
        const r = await fetch(`/api/cameras/${cam.id}/snapshot`, { credentials: "same-origin" });
        if (alive && r.status === 200) {
          const b = await r.blob();
          if (url) URL.revokeObjectURL(url);
          url = URL.createObjectURL(b);
          setSnap(url);
        }
      } catch { /* e'tibor bermaymiz */ }
      if (alive) timer = setTimeout(load, SNAP_REFRESH_MS);
    };
    load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      if (url) URL.revokeObjectURL(url);
    };
  }, [cam.id, cam.status, visible]);

  // Jonli bo'lish plitka ekranda ko'rinishiga BOG'LIQ EMAS: devorda faqat tanlangan
  // 4–36 ta kamera turadi (har biri ~0.11 Mbit/s substream), skroll qilinganda pastdagi
  // plitkalar o'chib, qaytganda sovuq qayta ulanardi — foydalanuvchi "kamera o'chib
  // qolyapti" deb ko'rardi.
  const live = wantLive && cam.status === "online";

  useEffect(() => {
    if (!live || !wsSupported()) { setState("idle"); return; }
    let handle = null, cancelled = false, retry = null;
    const again = (ms) => { retry = setTimeout(() => { if (!cancelled) setGen((g) => g + 1); }, ms); };
    setState("ulanish");
    (async () => {
      try {
        // PLITKALAR NAVBAT BILAN ULANADI. 9-36 ta oqim bir zumda ochilsa,
        // dekoderlar bir vaqtda sozlanadi va hub'lar GOP keshini birdaniga
        // yuboradi — oxirgi plitka doim yutqazib, "ulgurmadi" deb chetlanardi.
        if (idx) await new Promise((r) => setTimeout(r, idx * 150));
        if (cancelled) return;
        const hevcOk = await hevcSupported();
        // `sub=1` — devor plitkasi uchun KICHIK oqim (kameraning substream'i).
        // "Asosiy" tanlansa — to'liq sifat (og'ir: ko'p plitkada brauzer dekoderi ulgurmaydi).
        const j = await apiFetch(
          `/cameras/${cam.id}/stream?transport=ws${sub ? "&sub=1" : ""}${hevcOk ? "&hevc=1" : ""}`);
        if (cancelled) return;
        handle = await connectWs(j.ws_url, canvasRef.current, {
          // Devor plitkasi UZOQ kutmaydi: oqim bermaydigan kamera uchun hub 90s
          // (WS_STREAM_READY_TIMEOUT) kutadi va plitka shuncha vaqt bo'sh turardi
          // — foydalanuvchi "nega hammasi ochilmayapti" deb ko'rardi. 20s dan
          // keyin snapshot'ga tushamiz va keyinroq qayta urinamiz.
          maxWaitMs: TILE_WAIT_MS,
          // Oqim uzilsa plitka O'ZI qayta ulanadi. Avval "xato" deb qotib
          // qolardi: hub tomoshabinsiz 3s da yopiladi, ya'ni plitka boshqa
          // hech qachon tiklanmasdi — foydalanuvchi "devor uzilib qolyapti"
          // deb ko'rardi, o'sha kamerani alohida ochganda esa hammasi joyida.
          onEnd: () => { if (!cancelled) { setState("xato"); again(RETRY_MS); } },
          onStats: (s) => {
            beatRef.current = Date.now();
            onStats?.(cam.id, s);
          },
          // DIQQAT: devor plitkasi uchun "dekoder ulgurmadi" chegarasi YO'Q.
          // U 3200x1800 XOM oqim davri uchun yozilgan edi; plitka endi HAR DOIM
          // substream (640x360, ~0.11 Mbit/s) oladi, ya'ni brauzer chegarasi
          // emas. O'lchandi: chegara sog'lom kamerani ham 60s snapshot'ga
          // tushirar va har urinish SOVUQ boshlanish bo'lgani uchun u qayta
          // tiklanmasdi. Qotib qolgan oqimni endi vahima emas, WATCHDOG hal
          // qiladi — plitka jim qolsa qayta ulanadi ([[beatRef]]).
        });
        if (cancelled) { handle.close(); return; }
        beatRef.current = Date.now();
        setState("live");
        setCodec(handle.codec || "");
        onLiveChange?.(cam.id, true);
      } catch {
        // Ulanmadi (kamera oqim bermadi / bulut javob bermadi) — plitka
        // snapshot'da qoladi va ota komponent uni bir muddat chetlab o'tadi.
        if (!cancelled) { setState("xato"); onFail?.(cam.id); }
      }
    })();
    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
      if (handle) handle.close();
      onLiveChange?.(cam.id, false);
      onStats?.(cam.id, null);
    };
  }, [cam.id, live, idx, gen, sub, onLiveChange, onStats]);

  // WATCHDOG: jonli plitkadan STALL_MS davomida kadr kelmasa — qayta ulanamiz.
  // Kadr oqimi to'xtaganini `onStats` pulsining yo'qligidan bilamiz (u har 30
  // kadrda keladi). Bu "qotib qolgan rasm"ni o'zi tuzatadi.
  useEffect(() => {
    if (state !== "live") return;
    const t = setInterval(() => {
      if (Date.now() - beatRef.current > STALL_MS) setGen((g) => g + 1);
    }, 2000);
    return () => clearInterval(t);
  }, [state]);

  const isLive = state === "live";
  const badge = isLive ? `JONLI · ${(codec || "").toUpperCase()}`
    : state === "ulanish" ? "ulanmoqda…"
      : state === "xato" ? "qayta ulanmoqda…"
        : cam.status === "online" ? "OXIRGI KADR"
          : cam.status === "no_stream" ? "OQIM YO'Q" : "OFLAYN";

  return (
    <div className={`wall-tile ${isLive ? "is-live" : "is-snap"}`} ref={boxRef}
      role="button" tabIndex={0} title={`${cam.name} — to'liq ekranda ochish`}
      onClick={() => onOpen?.(cam)} onKeyDown={(e) => e.key === "Enter" && onOpen?.(cam)}>
      {snap && <img className="wall-snap" src={snap} alt="" style={{ opacity: isLive ? 0 : 1 }} />}
      <canvas ref={canvasRef} className="wall-canvas" style={{ display: isLive ? "block" : "none" }} />
      {!snap && !isLive && (
        <div className="wall-empty">
          {cam.status === "online" ? <IcCamera size={22} /> : <IcCameraOff size={22} />}
        </div>
      )}
      <div className="wall-top">
        <span className={`wall-badge ${isLive ? "on" : state === "sekin" ? "slow" : cam.status === "online" ? "warm" : "off"}`}>
          {isLive && <i className="wall-dot" />}{badge}
        </span>
      </div>
      <div className="wall-open" aria-hidden><IcExpand size={18} /><span>To'liq ekranda ochish</span></div>
      <div className="wall-name"><span>{cam.name}</span>{place && <em>{place}</em>}</div>
    </div>
  );
}

// Devor EKRANGA SIG'ADI va KENGLIKNI TO'LDIRADI. Qat'iy 16:9 bilan keng monitorda
// 4×4 o'rtada ingichka ustun bo'lib qolardi (yon tomonlar bo'm-bo'sh). Endi: kenglik
// to'liq, qator balandligi = mavjud balandlik / qatorlar; plitka 16:9 dan kengroq
// bo'lishi mumkin (rasm chetidan ozgina kesiladi, object-fit: cover), lekin 2.4:1 dan
// kengaymaydi — undan keyin to'r torayadi. Telefonda (<=760px) oddiy skroll.
const TILE_GAP = 10;
const MAX_ASPECT = 2.4;
function useFit(ref, gridCols, count, isFull, deps) {
  const [fit, setFit] = useState(null);   // { rowH, maxW } | null
  useEffect(() => {
    const calc = () => {
      const el = ref.current;
      // Haqiqiy ustunlar: telefonda CSS to'rni 2 ustunga majburlaydi (<=600px)
      const cols = window.innerWidth <= 600 ? Math.min(2, gridCols) : gridCols;
      const rows = Math.ceil(count / cols);
      // Oddiy rejimda sig'dirish faqat kompyuterda (>1024px); planshet/telefonda — to'liq kenglik + skroll
      if (!el || !rows || (!isFull && window.innerWidth <= 1024)) { setFit(null); return; }
      const gap = isFull ? 6 : TILE_GAP;
      const width = el.parentElement.clientWidth - (isFull ? 20 : 0);
      // Sahifa tepasidan masofa (skroll holatiga bog'liq emas); to'liq ekranda — panel
      const docTop = isFull ? 52 : el.getBoundingClientRect().top + window.scrollY + (document.querySelector(".main")?.scrollTop || 0);
      const availH = window.innerHeight - docTop - (isFull ? 10 : 20);
      const colW = (width - gap * (cols - 1)) / cols;
      let rowH = Math.min((availH - gap * (rows - 1)) / rows, colW * 9 / 16);
      // Juda past qator: oddiy rejimda skroll yaxshiroq; to'liq ekranda esa 16:9 bilan skroll
      if (rowH < (isFull ? 40 : 70)) { setFit(null); return; }
      let maxW = null;
      if (colW / rowH > MAX_ASPECT) {                    // juda cho'zilib ketmasin — to'r torayadi
        maxW = Math.floor(rowH * MAX_ASPECT * cols + gap * (cols - 1));
      }
      setFit({ rowH: Math.floor(rowH), maxW });
    };
    calc();
    window.addEventListener("resize", calc);
    return () => window.removeEventListener("resize", calc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridCols, count, isFull, ...deps]);
  return fit;
}

export default function Wall() {
  const facs = useList("/zones");
  const facById = useMemo(() => Object.fromEntries(facs.data.map((f) => [f.id, f])), [facs.data]);
  const [watch, setWatch] = useState(null);
  const wallRef = useRef(null);
  const [isFull, setIsFull] = useState(false);
  useEffect(() => {
    const on = () => setIsFull(document.fullscreenElement === wallRef.current);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wallRef.current?.requestFullscreen?.().catch(() => {});
  };

  const [cams, setCams] = useState([]);
  const [loading, setLoading] = useState(true);
  const [grid, setGrid] = useState(3);
  // Oqim sifati: "sub" (kichik substream, standart — ko'p plitkaga mos) yoki "main" (HD)
  const [quality, setQualityState] = useState(() => { try { return localStorage.getItem("vz_wall_q") || "sub"; } catch { return "sub"; } });
  const setQuality = (q) => { setQualityState(q); try { localStorage.setItem("vz_wall_q", q); } catch { /* */ } };
  // Devorda NECHTA kamera bo'lsin. Tanlangan son — devorning O'ZI: faqat shu
  // kameralar ko'rsatiladi va hammasi jonli bo'ladi. Qolganlari umuman
  // chizilmaydi (avval 122 plitka chiqib, faqat N tasi jonli edi — bu chalkash).
  const [count, setCount] = useState(9);
  const [page, setPage] = useState(0);
  const [q, setQ] = useState("");
  const [fac, setFac] = useState("");
  const [onlyOnline, setOnlyOnline] = useState(true);
  // STANDART HOLATDA JONLI OQIM O‘CHIQ. Sahifa ochilishi bilan 9 ta bulut
  // oqimini ko‘tarish xato: kamera batareyasi, 4G trafigi va bulut so‘rovlari
  // foydalanuvchi so‘ramagan holda sarflanadi. Avval snapshot devori ko‘rinadi,
  // jonli oqim faqat ANIQ bosilganda boshlanadi.
  const [liveOn, setLiveOn] = useState(false);
  // Katta oynada kamera ochilganda devor oqimlari to'xtaydi (ikki marta oqim tortilmasin)
  // Devor oqimlari faqat «Jonlini to'xtatish» bilan to'xtaydi: kamera katta oynada
  // ochilganda ham, vkladka fonda qolganda (alt+tab) ham davom etadi — foydalanuvchi
  // qaytganda devor sovuq qayta ulanishni kutmasin.
  const liveActive = liveOn;
  const [liveIds, setLiveIds] = useState(() => new Set());
  const [visibleIds, setVisibleIds] = useState(() => new Set());
  const [failed, setFailed] = useState(() => new Map());     // id -> vaqt (ulanmadi)
  const statsRef = useRef(new Map());
  const [fps, setFps] = useState(0);

  // Ro'yxat DAVRIY yangilanadi: kamera holati (online/offline) o'zgarsa devor
  // eskirgan holatda qolmasligi kerak — bir marta o'qilganda, o'sha lahzada
  // vaqtincha oflayn bo'lgan kameralar devorda umrbod yo'q bo'lib qolardi.
  useEffect(() => {
    let alive = true, timer = null;
    const load = () => apiList("/cameras?limit=500")
      .then((d) => { if (alive) setCams(Array.isArray(d) ? d : d.items || []); })
      .catch(() => { /* tarmoq xatosi — eski ro'yxat qoladi */ })
      .finally(() => {
        if (!alive) return;
        setLoading(false);
        timer = setTimeout(load, 30000);
      });
    load();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, []);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return cams.filter((c) => {
      if (onlyOnline && c.status !== "online") return false;
      if (fac && c.zone_id !== fac) return false;
      if (needle && !(c.name || "").toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [cams, q, fac, onlyOnline]);

  // DEVOR = tanlangan sahifadagi `count` ta kamera. Boshqa kameralar chizilmaydi;
  // ro'yxat bo'ylab yurish uchun sahifalash bor.
  const pages = Math.max(1, Math.ceil(rows.length / count));
  const curPage = Math.min(page, pages - 1);
  const shown = useMemo(
    () => rows.slice(curPage * count, curPage * count + count),
    [rows, curPage, count]);

  // Ko'rsatilgan plitkalarning HAMMASI jonli bo'lishi kerak. Faqat ikki istisno:
  //   * ekranda ko'rinmasa (skroll) — oqim ushlab turilmaydi;
  //   * kamera oqim BERMAGAN bo'lsa (ulanish xatosi) — 90s chetlanadi, keyin
  //     yana sinaladi. Bu MANBA muammosi, brauzer yuki emas.
  const liveSet = useMemo(() => {
    if (!liveActive) return new Set();
    const now = Date.now();
    const out = new Set();
    for (const c of shown) {
      const f = failed.get(c.id);
      if (f && now - f < FAIL_TTL_MS) continue;   // yaqinda ulanmagan — snapshot
      out.add(c.id);
    }
    return out;
  }, [shown, liveActive, failed]);

  // Ulanmagan kamera: bu BRAUZER emas, MANBA muammosi (kamera oqim bermadi) —
  // shuning uchun boshqa plitkalarga tegilmaydi, faqat shu kamera bir muddat
  // chetlanadi va keyin yana sinaladi.
  const onFail = useCallback((id) => {
    setFailed((prev) => new Map(prev).set(id, Date.now()));
  }, []);

  const onVisible = useCallback((id, on) => {
    setVisibleIds((prev) => {
      if (on === prev.has(id)) return prev;
      const next = new Set(prev);
      if (on) next.add(id); else next.delete(id);
      return next;
    });
  }, []);

  const onLiveChange = useCallback((id, on) => {
    setLiveIds((prev) => {
      const next = new Set(prev);
      if (on) next.add(id); else next.delete(id);
      return next;
    });
  }, []);

  const onStats = useCallback((id, s) => {
    if (s) statsRef.current.set(id, s); else statsRef.current.delete(id);
  }, []);

  // MUDDATI O'TGAN "chetlangan" plitkalarni TOZALAB turamiz — aks holda
  // `liveSet` faqat boshqa holat o'zgarganda qayta hisoblanadi va muddati
  // o'tgan plitka jonli bo'lmay turaverardi.
  useEffect(() => {
    const t = setInterval(() => {
      const now = Date.now();
      setFailed((prev) => {
        const live = [...prev].filter(([, at]) => now - at < FAIL_TTL_MS);
        return live.length === prev.size ? prev : new Map(live);
      });
    }, 5000);
    return () => clearInterval(t);
  }, []);

  // Umumiy kadr tezligi (diagnostika) — har 2s da hisoblanadi.
  useEffect(() => {
    const prev = new Map();
    const t = setInterval(() => {
      let total = 0;
      for (const [id, s] of statsRef.current) {
        const p = prev.get(id) || 0;
        if (s.frames > p) total += (s.frames - p) / 2;
        prev.set(id, s.frames);
      }
      setFps(Math.round(total));
    }, 2000);
    return () => clearInterval(t);
  }, []);

  const gridRef = useRef(null);
  const fit = useFit(gridRef, grid, shown.length, isFull, [loading, liveOn]);
  const openCam = useCallback((c) => setWatch(c), []);
  const closeCam = useCallback(() => setWatch(null), []);
  const pickCount = (n) => { setCount(n); setGrid(gridFor(n)); setPage(0); };

  return (
    <>
      <PageHeader
        eyebrow="Jonli devor"
        title="Kamera devori"
        subtitle={liveOn ? null : "Hozir oxirgi kadrlar ko'rsatilmoqda — jonli oqim uchun «Jonli boshlash» ni bosing."}
        tag={null}
        rightSlot={
          <div className="wall-actions">
            <button className={liveOn ? "btn" : "btn soft"} onClick={() => setLiveOn((v) => !v)}
              title={liveOn ? "Jonli oqimlarni to'xtatish" : `${shown.length} kamerani jonli ko'rish`}>
              {liveOn ? <><IcPause size={14} />Jonlini to'xtatish</> : <><IcPlay size={14} />Jonli boshlash</>}
            </button>
            <button className="btn ghost" onClick={toggleFull} title="Devorni to'liq ekranda ochish">
              <IcExpand size={15} /><span className="lbl">To'liq ekran</span>
            </button>
          </div>
        }
      />

      <div className="card wall-panel">
        <div className="wall-row">
          <div className="search-inline">
            <IcSearch size={15} />
            <input className="input" placeholder="Kamera qidirish…" value={q}
              onChange={(e) => { setQ(e.target.value); setPage(0); }} />
          </div>
          <select className="select" value={fac} onChange={(e) => { setFac(e.target.value); setPage(0); }}>
            <option value="">Barcha hududlar</option>
            {facs.data.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <label className="wall-check">
            <input type="checkbox" checked={onlyOnline} onChange={(e) => { setOnlyOnline(e.target.checked); setPage(0); }} />
            Faqat onlayn
          </label>
          <div className="spacer" />
          <div className="wall-stats">
            <span className="wall-legend" title="Hozir jonli oqayotgan plitkalar"><i className={`lg ${liveIds.size ? "live" : "snap"}`} />jonli <b>{liveIds.size}</b> / {shown.length}</span>
            {failed.size > 0 && (
              <span className="warn" title={`Bu kameralar oqim bermadi — oxirgi kadr ko'rsatilyapti, ${Math.round(FAIL_TTL_MS / 1000)} s dan keyin qayta urinadi`}>
                ulanmadi <b>{failed.size}</b>
              </span>
            )}
            {liveIds.size > 0 && <span title="Barcha jonli plitkalar bo'yicha">{fps} kadr/s</span>}
          </div>
        </div>
        <div className="wall-row sub">
          <div className="wall-ctl">
            <span className="cap">Kamera soni</span>
            <div className="seg">
              {COUNTS.map((n) => (
                <button key={n} className={count === n ? "on" : ""} onClick={() => pickCount(n)}>{n}</button>
              ))}
            </div>
          </div>
          <div className="wall-ctl">
            <span className="cap">To'r</span>
            <div className="seg">
              {GRIDS.map((g) => (
                <button key={g.n} className={grid === g.n ? "on" : ""} onClick={() => setGrid(g.n)}>{g.label}</button>
              ))}
            </div>
          </div>
          <div className="wall-ctl">
            <span className="cap">Sifat</span>
            <div className="seg" title="Sub — kameraning kichik oqimi (tez, ko'p plitkaga mos). Asosiy — to'liq sifat (og'ir: 16+ plitkada brauzer ulgurmasligi mumkin)">
              <button className={quality === "sub" ? "on" : ""} onClick={() => setQuality("sub")}>Sub</button>
              <button className={quality === "main" ? "on" : ""} onClick={() => setQuality("main")}>Asosiy (HD)</button>
            </div>
          </div>
          <div className="spacer" />
          {/* Devorda faqat `count` ta kamera turadi, qolganlariga shu tugmalar bilan
              o'tiladi (bir vaqtda faqat ko'rilayotganlar oqadi). */}
          <div className="wall-pager">
            <button className="btn-sq" disabled={curPage === 0} onClick={() => setPage((p) => Math.max(0, p - 1))} title="Oldingi devor"><IcChevronLeft size={15} /></button>
            <span className="mono">{rows.length ? curPage * count + 1 : 0}–{Math.min(rows.length, curPage * count + count)} / {rows.length}</span>
            <button className="btn-sq" disabled={curPage >= pages - 1} onClick={() => setPage((p) => Math.min(pages - 1, p + 1))} title="Keyingi devor"><IcChevronRight size={15} /></button>
          </div>
        </div>
      </div>

      {loading && <div className="empty">Yuklanmoqda…</div>}
      {!loading && rows.length === 0 && <div className="empty">Kamera topilmadi.</div>}

      <div className={`wall-stage${isFull ? " full" : ""}`} ref={wallRef}>
        {isFull && (
          <div className="wall-fullbar">
            <span>{rows.length ? curPage * count + 1 : 0}–{Math.min(rows.length, curPage * count + count)} / {rows.length}</span>
            <button className="btn-sq" disabled={curPage === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}><IcChevronLeft size={15} /></button>
            <button className="btn-sq" disabled={curPage >= pages - 1} onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}><IcChevronRight size={15} /></button>
            <button className="btn-sq" onClick={toggleFull} title="To'liq ekrandan chiqish"><IcShrink size={15} /></button>
          </div>
        )}
        <div className={`wall-grid${fit ? " fit" : ""}`} ref={gridRef}
          style={{ "--wcols": grid, gridAutoRows: fit ? `${fit.rowH}px` : undefined, maxWidth: fit?.maxW || undefined }}>
          {shown.map((c, i) => (
            <Tile key={c.id} cam={c} idx={i} wantLive={liveSet.has(c.id)}
              onLiveChange={onLiveChange} onStats={onStats} onVisible={onVisible}
              onFail={onFail} onOpen={openCam} place={facById[c.zone_id]?.name} sub={quality === "sub"} />
          ))}
        </div>
        {/* Katta oyna devor ICHIDA: to'liq ekranda faqat shu element ko'rinadi, tashqarida
            chizilgan oyna ko'rinmay qolardi ("bosganda ochilmayapti"). */}
        {watch && <Player fullscreen camera={watch} zoneName={facById[watch.zone_id]?.name} onClose={closeCam} />}
      </div>

    </>
  );
}
