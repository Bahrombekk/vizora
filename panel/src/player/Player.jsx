import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiFetch } from "../api/client";
import { connectWs, hevcSupported, rawBadAboveMp, rememberRawBadMp, wsSupported } from "./wsplayer.js";
import { IcClose, IcExpand, IcShrink } from "../components/icons.jsx";

const sColor = (s) => (s === "online" ? "var(--online)" : s === "error" ? "var(--error)" : "var(--offline)");

// hls.js bufer/barqarorlik sozlamasi. Maqsad: beqaror tarmoqda buzilishni
// kamaytirish — chuqurroq bufer (oldinga ~30s), stall'dan tiklash (nudge) va
// jonli chetdan biroz orqada turish (jitter zaxirasi). lowLatencyMode o'chiq —
// kechikishdan ko'ra barqarorlik muhim. ABR avtomatik (startLevel: -1).
const HLS_CONFIG = {
  // EZVIZ bulut kameralari ~15-25s "isiydi" (bulutga ulanish + birinchi kalit
  // kadrni kutish), shuning uchun manifest/level qayta urinishlari sabrli:
  // ~30 × 2s ≈ 60s sabr — warmup tugaguncha 404'larni bardosh beradi.
  // Qadam QISQA (0.7s), urinish KO'P: muxer tayyor bo'lgan zahoti ilib olamiz.
  // Oldin 2s qadam edi — sovuq startda o'rtacha 1s shunchaki bekorga ketardi.
  manifestLoadingMaxRetry: 60,
  manifestLoadingRetryDelay: 700,
  manifestLoadingMaxRetryTimeout: 4000,
  levelLoadingMaxRetry: 60,
  levelLoadingRetryDelay: 700,
  levelLoadingMaxRetryTimeout: 4000,
  fragLoadingMaxRetry: 10,
  // Boshlash tezligi: birinchi segmentni manifest kelishi bilan darhol so'raymiz.
  startFragPrefetch: true,
  // Bufer chuqurligi
  maxBufferLength: 30,
  maxMaxBufferLength: 60,
  backBufferLength: 30,
  maxBufferHole: 0.5,
  // Jonli oqim — chetdan 3 segment orqada. Segmentlar ~1s (publisher har 1s da
  // kalit kadr chiqaradi), demak ~3s jitter zaxirasi. 2 ta yetmadi: tarmoq yoki
  // enkoder bir zum kechikkanda pleyer ochlikdan to'xtab "zagruska" ko'rsatardi.
  // Boshlanish baribir tez (avval 2s segment × 2 = ~4-5s kutilardi).
  liveSyncDurationCount: 3,
  liveMaxLatencyDurationCount: 10,
  // LL-HLS: MediaMTX segment ichidagi 200ms "part"larni uzatadi va pleyer
  // segment YOPILISHINI kutmaydi -> ochilish ~3-4s emas, ~1s. Server tomonda
  // `hlsVariant: lowLatency` va proxy `_HLS_msn/_HLS_part` ni uzatishi kerak.
  // LL rejimida jonli chet `PART-HOLD-BACK` bo'yicha hisoblanadi, ya'ni
  // liveSyncDurationCount o'rniga part'lar ishlatiladi (yuqoridagi qiymat
  // faqat LL bo'lmagan playlistlarga taalluqli).
  lowLatencyMode: true,
  // Orqada qolsa SAKRAMASDAN tezlashib yetib oladi. 1.5x juda agressiv edi —
  // buferni tezroq bo'shatib qayta-qayta "zagruska"ga olib kelardi; 1.1x sezilmaydi
  // va buferni yemaydi.
  maxLiveSyncPlaybackRate: 1.1,
  // Stall'dan tiklash
  highBufferWatchdogPeriod: 1,
  nudgeMaxRetry: 10,
  // SD'dan boshlanadi -> birinchi kadr TEZ keladi (past tahmin -> eng past daraja).
  // Poster esa HD jonli-grab (chiroyli, aniq). So'ng ABR tarmoqqa qarab HD'ga
  // ko'tariladi. Tez ochilish ustun, poster HD bo'lib turadi.
  startLevel: -1,
  abrEwmaDefaultEstimate: 400000,
};

// DOIM TAYYOR kameralar uchun tezroq boshlash. Ularning oqimi allaqachon oqib
// turibdi va playlist to'la (7 segment), shuning uchun jonli chetga yaqinroq
// boshlaymiz: 2 emas, 1 segment buferlanadi (~1s tezroq). Oddiy (on-demand)
// kameralarda buni qilmaymiz — u yerda oqim endi tug'ilayotgan bo'ladi va
// chetga juda yaqin boshlash uzilishga olib keladi.
const HLS_CONFIG_WARM = { ...HLS_CONFIG, liveSyncDurationCount: 2 };

// --- WebRTC (WHEP) ---
// HLS'da pleyer 2 segment (~2s) buferlashi SHART; WebRTC'da birinchi kadr kelishi
// bilan ko'rsatiladi -> ochilish sezilarli tezroq, kechikish <1s (mobil kabi).
// WebRTC media UDP orqali boradi (MediaMTX ICE porti). Agar server o'sha portni
// ochmagan bo'lsa (yoki Cloudflare proxy ortida bo'lsa) ulanish BO'LMAYDI —
// shuning uchun natija eslab qolinadi va keyingi ochilishlarda bekorga
// kutilmaydi (jimgina HLS ishlatiladi), 10 daqiqada bir qayta sinaladi.
const WEBRTC_MEMO_KEY = "vz_webrtc_ok";
const WEBRTC_RETRY_MS = 10 * 60 * 1000;
const WEBRTC_TIMEOUT_MS = 4000;
// Necha KETMA-KET xatodan keyin WebRTC'ni vaqtincha o'chiramiz. Bitta transient
// xato (masalan birinchi kamera publisher hali tayyor bo'lmasdan ochilgan, yoki
// ICE bir zum kechikkan) WebRTC'ni butunlay o'chirmasligi kerak — aks holda
// WebRTC ishlaydigan muhitda ham hamma HLS'ga tushib, ~3s sekinlashardi.
// Faqat UDP HAQIQATAN bloklanganda (ketma-ket xato) o'chiramiz.
const WEBRTC_MAX_FAILS = 3;

function webrtcWorthTrying() {
  if (typeof RTCPeerConnection === "undefined") return false;
  try {
    const raw = localStorage.getItem(WEBRTC_MEMO_KEY);
    if (!raw) return true;                       // hali sinalmagan
    const { fails = 0, ts = 0 } = JSON.parse(raw);
    if (fails < WEBRTC_MAX_FAILS) return true;   // hali "o'chirish" chegarasiga yetmadi
    return Date.now() - ts > WEBRTC_RETRY_MS;    // ko'p marta yiqilgan — TTL'dan keyin qayta
  } catch { return true; }
}

function rememberWebrtc(ok) {
  try {
    if (ok) {
      localStorage.setItem(WEBRTC_MEMO_KEY, JSON.stringify({ fails: 0, ts: Date.now() }));
      return;
    }
    let fails = 0;
    try { fails = (JSON.parse(localStorage.getItem(WEBRTC_MEMO_KEY)) || {}).fails || 0; }
    catch { /* buzuq/yo'q */ }
    localStorage.setItem(WEBRTC_MEMO_KEY, JSON.stringify({ fails: fails + 1, ts: Date.now() }));
  } catch { /* private rejim — e'tibor bermaymiz */ }
}

// ICE yig'ilishini kutamiz (trickle'siz: bitta to'liq SDP yuboramiz — MediaMTX
// javobida "end-of-candidates" beradi, PATCH kerak bo'lmaydi).
function waitIceComplete(pc, ms = 2000) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { clearTimeout(t); pc.removeEventListener("icegatheringstatechange", h); resolve(); };
    const h = () => { if (pc.iceGatheringState === "complete") done(); };
    pc.addEventListener("icegatheringstatechange", h);
    const t = setTimeout(done, ms);   // sabrsiz bo'lmaymiz, lekin cheksiz kutmaymiz
  });
}

/** WHEP orqali ulanadi. Muvaffaqiyat: {pc, location}. Xato: throw. */
async function connectWhep(whepUrl, video) {
  const pc = new RTCPeerConnection({ iceServers: [], bundlePolicy: "max-bundle" });
  try {
    // Faqat VIDEO: publisher ffmpeg'da `-an` (audio yo'q) — audio m-line so'rasak
    // MediaMTX uni rad etadi va ba'zi brauzerlarda ulanish sekinlashadi.
    pc.addTransceiver("video", { direction: "recvonly" });
    const ms = new MediaStream();
    pc.addEventListener("track", (e) => {
      ms.addTrack(e.track);
      video.srcObject = ms;
    });

    await pc.setLocalDescription(await pc.createOffer());
    await waitIceComplete(pc);

    const res = await fetch(whepUrl, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`WHEP ${res.status}`);
    const answer = await res.text();
    const location = res.headers.get("Location");
    await pc.setRemoteDescription({ type: "answer", sdp: answer });

    // Ulanish HAQIQATAN qurilishini kutamiz — UDP yopiq bo'lsa shu yerda tugaydi.
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("WebRTC ulanmadi (UDP yopiqmi?)")), WEBRTC_TIMEOUT_MS);
      const check = () => {
        const st = pc.connectionState;
        if (st === "connected") { clearTimeout(t); resolve(); }
        else if (st === "failed" || st === "closed") { clearTimeout(t); reject(new Error("WebRTC " + st)); }
      };
      pc.addEventListener("connectionstatechange", check);
      check();
    });
    return { pc, location };
  } catch (e) {
    try { pc.close(); } catch { /* ignore */ }
    throw e;
  }
}

export default function Player({ camera, zoneName = "", onClose, fetchStream, fullscreen = false }) {
  // To'liq ekran: `fullscreen` (devordan ochilganda) yoki oynadagi tugma bilan.
  // Brauzer Fullscreen API ham so'raladi (bosishdan keyingi ~5s faollik oynasida ishlaydi);
  // ruxsat berilmasa ham oyna butun ko'rinish maydonini egallaydi.
  const rootRef = useRef(null);
  // Portal manzili OCHILISHDA bir marta: to'liq ekrandagi devor ichida yoki body. Har
  // chizishda qayta olinsa, oynaning O'ZI to'liq ekranga o'tgach manzil = oynaning o'zi
  // bo'lib qolardi va React uni o'z ichiga ko'chirib buzardi ("to'liq ekran ishlamayapti").
  const [portalTo] = useState(() => document.fullscreenElement || document.body);
  const [full, setFull] = useState(fullscreen);
  const ownFs = useRef(false);   // brauzer to'liq ekranini shu oyna yoqdimi
  useEffect(() => {
    if (!full) return;
    // Devor allaqachon to'liq ekranda bo'lsa — uni buzmaymiz (oyna uning ICHIDA ochiladi)
    if (!document.fullscreenElement && rootRef.current?.requestFullscreen) {
      rootRef.current.requestFullscreen().then(() => { ownFs.current = true; }).catch(() => {});
    }
  }, [full]);
  // DIQQAT: ota komponent (devor) har ~2s qayta chiziladi va har safar YANGI `onClose`
  // beradi. Effekt unga bog'langanda har safar tozalanib, to'liq ekrandan CHIQARIB
  // yuborardi — chiqish hodisasi esa oynani yopardi: "kamera to'liq ekranda ochilmayapti"
  // (ochilib darhol yopilardi). Shuning uchun onClose ref orqali, tinglovchi bir marta.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    // Brauzer to'liq ekrandan (Esc) chiqilsa — shu oyna yoqqan bo'lsa, oyna ham yopiladi
    const onFs = () => {
      if (ownFs.current && !document.fullscreenElement) { ownFs.current = false; if (fullscreen) closeRef.current?.(); else setFull(false); }
    };
    const onKey = (e) => { if (e.key === "Escape" && !document.fullscreenElement) closeRef.current?.(); };
    document.addEventListener("fullscreenchange", onFs);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("fullscreenchange", onFs);
      window.removeEventListener("keydown", onKey);
      // faqat oyna YOPILGANDA (unmount) — shu oyna yoqqan to'liq ekrandan chiqamiz
      if (ownFs.current && document.fullscreenElement) { ownFs.current = false; document.exitFullscreen?.().catch(() => {}); }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const toggleFull = () => {
    if (full) {
      if (ownFs.current && document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
      ownFs.current = false;
      if (fullscreen) onClose?.(); else setFull(false);
    } else setFull(true);
  };

  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  // "ws" — WebSocket+WebCodecs (canvas), "video" — WebRTC/HLS (<video>)
  const [mode, setMode] = useState("video");
  const ezHint = camera.source_type === "ezviz" ? " (EZVIZ bulut — 15-25s isidi)" : "";
  const [status, setStatus] = useState("Ulanmoqda…" + ezHint);
  const [quality, setQuality] = useState(null); // {name, abr} — ABR oqimlar uchun
  const [poster, setPoster] = useState(null);   // snapshot — yuklanayotganda ko'rsatiladi
  const [live, setLive] = useState(false);       // HAQIQIY kadr ekranga chiqdimi

  // EZVIZ snapshot'ni poster sifatida olamiz. fresh=1 — kamera qizigach jonli
  // oqimdan kadr olinadi (o'lcham/sifat jonli video bilan AYNAN bir xil). Jonli
  // kelguncha bir necha marta yangilab turamiz (avval kesh, keyin yangi grab).
  useEffect(() => {
    if (camera.source_type !== "ezviz") return;
    let alive = true, obj, timer, tries = 0;
    const load = async () => {
      try {
        const r = await fetch(`/api/cameras/${camera.id}/snapshot?fresh=1`, { credentials: "same-origin" });
        if (alive && r.status === 200) {
          const blob = await r.blob();
          if (obj) URL.revokeObjectURL(obj);
          obj = URL.createObjectURL(blob);
          setPoster(obj);
        }
      } catch { /* e'tibor bermaymiz */ }
      tries += 1;
      if (alive && tries < 8) timer = setTimeout(load, 3000); // jonli kelguncha yangilab tur
    };
    load();
    return () => { alive = false; if (timer) clearTimeout(timer); if (obj) URL.revokeObjectURL(obj); };
  }, [camera.id, camera.source_type]);

  useEffect(() => {
    const video = videoRef.current;
    let hls;
    let pc = null;              // WebRTC ulanishi (WHEP)
    let whepLocation = null;    // sessiyani yopish uchun (DELETE)
    let wsHandle = null;        // WebSocket+WebCodecs ulanishi
    let cancelled = false;
    let reloadTimer = null;
    let reloads = 0;
    const MAX_RELOADS = 30; // ~1 daqiqa davomida tiklashga urinish

    // Poster'ni HAQIQIY kadr ekranga chiqquncha ushlab turamiz. manifest yuklanishi
    // (status="") yetarli EMAS — kadr hali dekod bo'lmagan. requestVideoFrameCallback
    // kadr KOMPOZITSIYAGA chiqqanda aniq signal beradi; bo'lmasa "playing" eventi.
    setLive(false);
    let rvfc;
    const markLive = () => setLive(true);
    if (video.requestVideoFrameCallback) {
      rvfc = video.requestVideoFrameCallback(markLive);
    }
    video.addEventListener("playing", markLive);

    // Tokenli oqim URL'larini olamiz (oqim himoyalangan). Admin uchun
    // /cameras/{id}/stream, iste'molchi (Integratsiya) uchun fetchStream.
    // `transport=webrtc` — backend HLS muxerini isitib segment kutmaydi (~2s tejash);
    // WebRTC sinalmasa oddiy (HLS) yo'l so'raladi.
    // 0) WebSocket+WebCodecs — eng tez: segment ham, UDP ham yo'q, so'nggi GOP
    //    bir zumda keladi. Brauzer kodekni qo'llamasa (isConfigSupported) jimgina
    //    WebRTC/HLS'ga tushamiz. Iste'molchi (Integratsiya) rejimida ishlatilmaydi.
    const tryWs = !fetchStream && wsSupported();
    const tryWebrtc = !fetchStream && webrtcWorthTrying();
    const transport = tryWs ? "ws" : tryWebrtc ? "webrtc" : "";
    // `hevc=1` — brauzer xom HEVC'ni dekodlay oladi, demak backend publisher'ni
    // ENKODERSIZ boshlaydi (CPU ~1 yadro o'rniga ~0.02) va `<path>/raw` beradi.
    // Dekoder ulgurmasa (katta o'lcham) quyida bu bayroq o'chiriladi va keyingi
    // so'rovda backend to'liq transkodga o'tadi.
    let askHevc = true;
    const load = fetchStream || (async () => {
      const hevcOk = askHevc && await hevcSupported();
      const qs = transport ? `?transport=${transport}${hevcOk ? "&hevc=1" : ""}` : "";
      return apiFetch(`/cameras/${camera.id}/stream${qs}`);
    });

    // To'liq qayta yuklash: /stream'ni qayta so'raymiz (yangi token + yangi HLS
    // sessiya; EZVIZ publisher o'lgan bo'lsa qayta tirilab, tayyor bo'lguncha
    // kutadi), so'ng hls.js'ni yangi manbaga ulaymiz. Bu MediaMTX HLS sessiyasi
    // eskirib 401 berganda (publisher uzilib-ulanganda) "qotib qolish"ni tiklaydi
    // — eski startLoad() esa o'sha eskirgan sessiyani qayta urib, qotib qolardi.
    function closePc() {
      // Sessiyani serverda ham yopamiz — MediaMTX reader'i bo'sh qolsin (aks holda
      // idle-reaper kamerani "ko'rilmoqda" deb hisoblab uzoq issiq tutadi).
      if (whepLocation) {
        try {
          fetch(whepLocation, { method: "DELETE", credentials: "same-origin", keepalive: true });
        } catch { /* ignore */ }
        whepLocation = null;
      }
      if (pc) { try { pc.close(); } catch { /* ignore */ } pc = null; }
      video.srcObject = null;
    }
    function closeWs() {
      if (wsHandle) { try { wsHandle.close(); } catch { /* ignore */ } wsHandle = null; }
    }

    function reload(msg) {
      if (cancelled || reloadTimer) return;
      if (reloads >= MAX_RELOADS) { setStatus("Xato: oqimni tiklab bo'lmadi."); return; }
      reloads += 1;
      closeWs();
      closePc();       // eski WebRTC sessiyasini yopamiz (yangi urinish toza bo'lsin)
      setLive(false);  // oqim uzildi -> poster (oxirgi snapshot) yana ko'rinsin
      setStatus(msg + ezHint);
      reloadTimer = setTimeout(() => { reloadTimer = null; if (!cancelled) start(); }, 1000);
    }

    // WebRTC muvaffaqiyatli bo'lsa HLS'ga umuman o'tmaymiz.
    // hls.js (~0.5 MB) faqat shu zaxira yo'lda kerak — WS/WebRTC ishlasa yuklanmaydi.
    async function startHls(hls_url) {
      const { default: Hls } = await import("hls.js");
      if (cancelled) return;
      video.srcObject = null;   // WebRTC urinishidan qolgan stream tozalanadi
      if (Hls.isSupported()) {
        if (hls) hls.destroy();
        hls = new Hls(camera.always_warm ? HLS_CONFIG_WARM : HLS_CONFIG);
        hls.loadSource(hls_url);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, (_e, data) => {
          reloads = 0; // sog'lom ulanish — tiklash hisoblagichini nollaymiz
          setStatus(""); video.play().catch(() => {});
          if (data?.levels?.length > 1) setQuality({ name: "AUTO", abr: true });
        });
        hls.on(Hls.Events.LEVEL_SWITCHED, (_e, data) => {
          const lvl = hls.levels?.[data.level];
          if (lvl && hls.levels.length > 1) {
            const tag = lvl.name || (lvl.height ? `${lvl.height}p` : "");
            setQuality({ name: hls.autoLevelEnabled ? `AUTO · ${tag}` : tag, abr: true });
          }
        });
        hls.on(Hls.Events.ERROR, (_e, data) => {
          if (!data.fatal) return;
          // Media xatosi — sessiyani yo'qotmasdan tiklashga urinamiz.
          if (data.type === Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
          // Tarmoq (401/404/timeout) yoki boshqa halokatli xato: HLS sessiya
          // yoki publisher yo'qolgan — to'liq qayta yuklaymiz (yangi sessiya).
          reload("Oqim tiklanmoqda, kuting…");
        });
      } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = hls_url;
        video.addEventListener("loadedmetadata", () => { reloads = 0; setStatus(""); });
        video.addEventListener("error", () => reload("Oqim tiklanmoqda, kuting…"), { once: true });
      } else setStatus("Brauzer HLS'ni qo'llab-quvvatlamaydi.");
    }

    function start() {
      load()
        .then(async ({ hls_url, whep_url, ws_url, ws_raw }) => {
          if (cancelled) return;
          // 0) WebSocket + WebCodecs — birinchi kadr darhol (so'nggi GOP burst).
          //    `ws_raw` — backend enkodersiz rejimda: bu path'da kameraning ASL
          //    oqimi (odatda HEVC, to'liq o'lcham) keladi.
          for (const wsPick of (tryWs && ws_url && canvasRef.current ? [ws_url] : [])) {
            try {
              const isRaw = !!ws_raw;
              const okFrom = Date.now();
              const h = await connectWs(wsPick, canvasRef.current, {
                onEnd: () => {
                  if (cancelled) return;
                  // Xom (HEVC) oqim TEZ uzildi — deyarli har doim brauzer dekoderi
                  // ko'tara olmagani (apparat HEVC yo'q): 3200x1800 da dekoder ~1s
                  // da xato beradi. Uni belgilab, H.264 ga o'tamiz. Kech uzilish
                  // esa oddiy tarmoq/publisher hodisasi — xom yo'l aybdor emas.
                  if (isRaw && Date.now() - okFrom < 10000 && h?.w) {
                    rememberRawBadMp(h.w * h.h / 1e6);
                    askHevc = false;   // keyingi so'rovda backend transkodga o'tadi
                  }
                  reload("Oqim tiklanmoqda, kuting…");
                },
                // Publisher (bulut) hali oqim bermayapti — foydalanuvchi ko'rsin
                onWait: (s) => { if (!cancelled) setStatus(`Kamera oqimi kutilmoqda… ${Math.round(s)}s${ezHint}`); },
                // Xom (HEVC) oqimni bu brauzer chizishga ULGURMAYAPTI (apparat
                // dekoder yo'q) — H.264 ga qaytamiz va buni eslab qolamiz.
                onUnhealthy: isRaw ? (st) => {
                  if (cancelled) return;
                  console.debug("Xom HEVC og'ir keldi, H.264 ga o'tamiz:", st);
                  if (h?.w) rememberRawBadMp(h.w * h.h / 1e6);
                  askHevc = false;     // backend to'liq transkodga o'tsin
                  reload("Sifat moslashtirilmoqda…");
                } : undefined,
              });
              if (cancelled) { h.close(); return; }
              // Shu brauzer AVVAL aynan shunday (yoki kattaroq) o'lchamdagi xom
              // oqimni ko'tara olmagan — birinchi kadrdanoq H.264 ga o'tamiz,
              // foydalanuvchi qotishni umuman ko'rmaydi. Kichikroq o'lchamlar
              // (masalan 1440p) esa xom bo'lib qolaveradi.
              if (isRaw && h.w && (h.w * h.h) / 1e6 >= rawBadAboveMp()) {
                h.close();
                askHevc = false;
                reload("Sifat moslashtirilmoqda…");
                return;
              }
              wsHandle = h;
              reloads = 0;
              setMode("ws");
              setLive(true);          // birinchi kadr canvas'ga CHIZILGAN — poster ketadi
              setStatus("");
              setQuality({ name: `WS · ${(h.codec || "").toUpperCase()}`, abr: false });
              return;
            } catch (e) {
              closeWs();
              if (cancelled) return;
              // Manba oqim bermadi (kamera oflayn / publisher o'ldi): WebRTC ham,
              // HLS ham AYNI shu manbani o'qiydi — fallback behuda va qora ekran
              // + proxy'ni bekorga uradi. Aniq xabar ko'rsatamiz.
              if (e?.sourceFailed) {
                setStatus("Kamera oqim bermayapti — oflayn yoki bulutga ulanmadi.");
                return;
              }
              console.debug("WS pleyer ishlamadi, WebRTC/HLS'ga o'tamiz:", e?.message);
            }
          }
          setMode("video");
          // 1) WebRTC — tezroq (segment kutish yo'q). Ulanmasa jimgina HLS.
          if (tryWebrtc && whep_url) {
            try {
              const conn = await connectWhep(whep_url, video);
              if (cancelled) { try { conn.pc.close(); } catch { /* ignore */ } return; }
              pc = conn.pc;
              whepLocation = conn.location;
              rememberWebrtc(true);
              reloads = 0;
              setStatus("");
              setQuality({ name: "WebRTC", abr: false });
              video.play().catch(() => {});
              // Ulanish keyinroq uzilsa (tarmoq) — to'liq qayta yuklaymiz.
              pc.addEventListener("connectionstatechange", () => {
                if (cancelled || !pc) return;
                const st = pc.connectionState;
                if (st === "failed" || st === "disconnected") reload("Oqim tiklanmoqda, kuting…");
              });
              return;
            } catch {
              rememberWebrtc(false);   // keyingi ochilishlarda bekorga kutmaymiz
              closePc();
              if (cancelled) return;
              // HLS'ga qaytamiz (quyida).
            }
          }
          await startHls(hls_url);
        })
        .catch((e) => {
          // Ruxsat/sozlama xatosi qayta urinishda hech qachon tuzalmaydi —
          // cheksiz "tayyorlanmoqda" o'rniga aniq xabar ko'rsatamiz.
          if (e?.status === 401 || e?.status === 403) {
            setStatus("Bu API kalitida bu kameraga ruxsat yo'q.");
          } else if (e?.status === 400) {
            setStatus("Xato: " + e.message);
          } else {
            reload("Oqim tayyorlanmoqda, kuting…");
          }
        });
    }

    start();

    return () => {
      cancelled = true;
      if (reloadTimer) clearTimeout(reloadTimer);
      closeWs();
      closePc();
      if (hls) hls.destroy();
      video.removeEventListener("playing", markLive);
      if (rvfc && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(rvfc);
    };
  }, [camera.id, fetchStream]);

  const clock = new Date().toLocaleTimeString("ru-RU");

  // Oyna body'ga (yoki to'liq ekrandagi elementga) chiziladi: sahifa ichida qolsa
  // .content stacking-context'i tufayli to'liq ekran oynasi chap menyu OSTIDA qolardi.
  return createPortal(
    <div className={`modal-overlay${full ? " pf-overlay" : ""}`} onMouseDown={full ? undefined : onClose}>
      <div ref={rootRef} className={`modal wide clip${full ? " pf-modal" : ""}`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="title">
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: sColor(camera.status) }} />
            {camera.name}
            {zoneName && <span style={{ fontSize: 13, fontWeight: 400, color: "var(--dim)" }}>· {zoneName}</span>}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn-sq" onClick={toggleFull} title={full ? "Kichraytirish" : "To'liq ekran"}>
              {full ? <IcShrink size={16} /> : <IcExpand size={16} />}
            </button>
            <button className="btn-sq" onClick={onClose} title="Yopish (Esc)"><IcClose size={16} /></button>
          </div>
        </div>

        <div className="pv-box" style={{ position: "relative", aspectRatio: "16/9", background: "#05080d", overflow: "hidden" }}>
          <video ref={videoRef} controls autoPlay muted playsInline
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: full ? "contain" : "cover", background: "#000", visibility: mode === "ws" ? "hidden" : "visible" }} />
          {/* WebCodecs chiqishi — <video> emas, canvas (kadrlar to'g'ridan chiziladi) */}
          <canvas ref={canvasRef}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: full ? "contain" : "cover", background: "#000", display: mode === "ws" ? "block" : "none" }} />
          <div className="scan-lines" style={{ pointerEvents: "none", opacity: 0.5 }} />
          <div className="scan-sweep" style={{ pointerEvents: "none", height: "35%", animationDuration: "6s" }} />
          <div style={{ position: "absolute", top: 14, left: 16, fontFamily: "var(--mono)", fontSize: 13, color: "rgba(255,255,255,.85)", textShadow: "0 1px 3px rgba(0,0,0,.7)", pointerEvents: "none" }}>{clock}</div>
          <div style={{ position: "absolute", top: 14, right: 16, display: "flex", alignItems: "center", gap: 8, pointerEvents: "none" }}>
            {quality && (
              <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "4px 10px", borderRadius: 6, background: "rgba(0,0,0,.5)" }} title="Sifat tarmoqqa qarab avto-moslashadi">
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, fontWeight: 600, color: "#7dd3fc", letterSpacing: 0.5 }}>{quality.name}</span>
              </div>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 6, background: "rgba(0,0,0,.5)" }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#ef4444", animation: "recpulse 1.4s infinite" }} />
              <span style={{ fontFamily: "var(--mono)", fontSize: 11, fontWeight: 600, color: "#fff", letterSpacing: 1 }}>REC</span>
            </div>
          </div>
          {poster && (
            <img src={poster} alt="" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: full ? "contain" : "cover", background: "#000", pointerEvents: "none", opacity: live ? 0 : 1, transition: "opacity .6s ease" }} />
          )}
          {status && (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "flex-end", justifyContent: "center", color: "#fff", fontSize: 13, textAlign: "center", padding: "0 0 26px", pointerEvents: "none" }}>
              <span style={{ padding: "6px 14px", borderRadius: 8, background: "rgba(0,0,0,.55)", backdropFilter: "blur(2px)" }}>{status}</span>
            </div>
          )}
        </div>

        <div className="pv-foot" style={{ padding: "13px 20px", fontSize: 12.5, color: "var(--dim)", display: "flex", gap: 14 }}>
          <span>Kodek: <b style={{ color: "var(--text)" }}>{camera.codec}{camera.transcode ? " → H.264" : ""}</b></span>
          {camera.has_sub && camera.source_type !== "ezviz" && (
            <span title="Sub-stream asosiy oqimdan mustaqil: H.264 bo'lsa transkodsiz uzatiladi">
              Sub: <b style={{ color: "var(--text)" }}>
                {camera.sub_codec === "auto" ? "aniqlanmoqda" : camera.sub_codec}
                {camera.sub_transcode ? " → H.264" : " · transkodsiz"}
              </b>
            </span>
          )}
          {camera.stream_path && <span className="mono">{camera.stream_path}</span>}
          {camera.branch_name && <span className="mono">{camera.branch_name}</span>}
        </div>
      </div>
    </div>,
    portalTo,
  );
}
