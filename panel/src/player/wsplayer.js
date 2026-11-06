// WebSocket + WebCodecs pleyer — kodlangan kadrlar WS orqali keladi, brauzer
// `VideoDecoder` bilan dekodlab canvas'ga chizadi. Segment yo'q, UDP yo'q:
// birinchi kadr kelishi bilan tasvir chiqadi. Backend: vizora/media/ws_stream.py.
//
// Xabar formati: JSON {type:"init", codec, codecString} / {type:"end", reason};
// binary: [0] bayroq (bit0 = kalit), [4..11] timestamp µs (uint64 BE), keyin Annex-B.

const HDR = 12;
// Dekoder navbati shundan oshsa — KALIT KADRGACHA tashlaymiz (jonliga sakrash).
// Bu XAVFSIZLIK KLAPANI, normal ish rejimi emas: bulut kamerasi butun GOP'ni
// (25 fps × 2s = ~50 kadr) bir zumda yuboradi, ya'ni navbat har burstda
// vaqtincha 50 gacha ko'tariladi. Chegara 12 (keyin 24) bo'lganda har burstning
// yarmi TASHLANARDI — aynan shu "qotib-sakrab" ko'rinishning yarmi shundan edi.
// Endi bufer chizishni tekislaydi ([[PACE_DELAY_MS]]), navbat esa faqat
// dekoder haqiqatan ulgurmaganda (~5s zaxira) tozalanadi.
const MAX_QUEUE = 120;
// EZVIZ sovuq start 6-20s (bulut + publisher); backend hub manbani 45s gacha kutadi.
// Shu vaqt ichida kadr kelmasa WebRTC/HLS'ga tushamiz (ular ham shu manbani kutadi).
const FIRST_FRAME_TIMEOUT = 40000;

// --- Chizishni O'LCHOVLI qilish (jitter bufer) ---
// Bulut kameralari kadrlarni tekis emas, butun GOP'ni bir zumda yuboradi
// (o'lchandi: 50 kadr <10ms ichida, keyin 2s jimlik). Kadr kelishi bilan
// chizilsa — tasvir qotadi va sakraydi. Kadr timestamp'i bo'yicha chizsak,
// o'sha 2 soniyalik burst 2 soniyaga tekis yoyiladi.
const PACE_TARGET_MS = 900;       // maqsad bufer (~1 GOP) — shu atrofda ushlanadi
const PACE_RATE_WINDOW_MS = 4000; // kelish tezligi shu oynada o'lchanadi
// Bufer cheki. Ulanishda hub butun GOP keshini yuboradi (~2s) va manba ham
// 2s'lik burstlar bilan keladi — ya'ni navbat normal ishda ham ~100 kadrga
// chiqishi mumkin. Chegara past bo'lsa har burstda "jonliga sakrash" ishlab
// ketardi va tasvir yana sakrab ko'rinardi.
const PACE_MAX_FRAMES = 150;      // ~6s — bundan oshsa haqiqatan orqada qolganmiz
const PACE_TRIM_FRAMES = 50;      // shuncha (~2s) qoldirib, qolganini tashlaymiz

// Brauzer HEVC'ni dekodlay oladimi (odatda apparat dekoder kerak). Natija bir
// marta hisoblanadi. Qo'llasa — pleyer TRANSKODSIZ `/raw` oqimini oladi: sovuq
// ochilish ~1.4s tezroq va tasvir qayta kodlanmagan (sifat yo'qolmaydi).
let _hevcPromise = null;
export function hevcSupported() {
  if (!_hevcPromise) {
    _hevcPromise = (typeof VideoDecoder === "undefined")
      ? Promise.resolve(false)
      : VideoDecoder.isConfigSupported({ codec: "hev1.1.6.L93.B0" })
        .then((r) => !!r.supported).catch(() => false);
  }
  return _hevcPromise;
}

// --- Xom (HEVC) oqim bu brauzerda HAQIQATAN chizila oladimi ---
// `isConfigSupported` DASTURIY dekoderni ham "qo'llanadi" deb aytadi. O'lchandi:
// 3200x1800 HEVC da brauzer 1449 kadrdan faqat 269 tasini chizdi (4.5 fps) —
// ya'ni apparat dekoder yo'q va tasvir "qotib" ko'rinadi; 2560x1440 esa 25 fps
// da toza ishladi. Shuning uchun ulangandan keyin dekoder ULGURAYOTGANINI
// o'lchaymiz va ulgurmasa H.264 ga qaytamiz. Natija eslab qolinadi.
// Chegara O'LCHAM bo'yicha eslab qolinadi, "umuman xom yo'q" deb emas: o'lchandi —
// bir xil brauzerda 2560x1440 HEVC 25 fps da toza chizildi, 3200x1800 esa qotdi.
// Hammasini o'chirish 1440p kameralarni ham sifatsiz qilardi.
const RAW_BAD_KEY = "vz_ws_raw_bad_mp";
const RAW_BAD_TTL = 30 * 60 * 1000;
// SOG'LIQNI BOSHLANISHDA O'LCHAMAYMIZ. Birinchi soniyalar — eng yomon payt:
// hub GOP keshini BIR ZUMDA yuboradi, dekoder endi sozlanadi, devorda esa 9-36
// plitka bir vaqtda ulanadi. Shu oynada "chizilgan/kelgan" nisbati doim past
// chiqadi va sog'lom kamera ham "ulgurmadi" deb 60s snapshot'ga tushardi —
// keyingi urinish yana SOVUQ boshlanish bo'lgani uchun yana tushardi, ya'ni
// bitta plitka umrbod o'lik qolardi (o'lchandi: yolg'iz o'zi nisbat 1.82,
// devorda esa 0.50). Shuning uchun: avval tinchlanish, keyin ketma-ket oynalar.
const HEALTH_SETTLE_MS = 2500;    // boshlanish (burst + sozlanish) hisobga olinmaydi
const HEALTH_WINDOW_MS = 3000;    // keyin har shuncha vaqtlik oyna o'lchanadi
const HEALTH_MIN_RATIO = 0.6;     // chizilgan/kelgan shundan past bo'lsa — yomon oyna
const HEALTH_STRIKES = 2;         // KETMA-KET shuncha yomon oyna = haqiqiy muammo

/** Qaysi megapiksellardan boshlab xom oqim bu brauzerda og'irlik qilgan (Infinity = muammo yo'q). */
export function rawBadAboveMp() {
  try {
    const v = JSON.parse(localStorage.getItem(RAW_BAD_KEY) || "null");
    if (!v || Date.now() - v.ts > RAW_BAD_TTL) return Infinity;
    return v.mp;
  } catch { return Infinity; }
}

export function rememberRawBadMp(mp) {
  try {
    const cur = rawBadAboveMp();
    const val = Math.min(cur === Infinity ? mp : cur, mp);
    localStorage.setItem(RAW_BAD_KEY, JSON.stringify({ mp: val, ts: Date.now() }));
  } catch { /* private rejim */ }
}

export function wsSupported() {
  return typeof WebSocket !== "undefined" && typeof VideoDecoder !== "undefined"
    && typeof EncodedVideoChunk !== "undefined";
}

/**
 * Ulanadi va kadrlarni `canvas`ga chizadi.
 * @returns {Promise<{close():void, codec:string}>} — birinchi kadr chizilganda resolve;
 *   ulanmasa/kodek qo'llanmasa reject (chaqiruvchi WebRTC/HLS'ga qaytadi).
 * onEnd(reason) — oqim keyinroq uzilsa (reload uchun).
 */
/**
 * @param maxWaitMs Birinchi kadr uchun QAT'IY muddat. `onWait` puls kelganda
 *   oddiy taymer qayta boshlanadi (bulut 15-25s isiydi), lekin devor plitkasi
 *   bunchalik kuta olmaydi: oqim bermaydigan kamera uchun hub 90s (
 *   `WS_STREAM_READY_TIMEOUT`) kutadi va plitka shuncha vaqt bo'sh turadi.
 *   Berilsa — shu muddatdan keyin baribir taslim bo'lamiz.
 */
export function connectWs(url, canvas, { onEnd, onStats, onWait, onUnhealthy, maxWaitMs } = {}) {
  return new Promise((resolve, reject) => {
    const wsUrl = url.startsWith("/")
      ? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${url}` : url;
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    const ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
    let decoder = null;
    let codec = "";
    let needKey = true;
    let settled = false;
    let closed = false;
    let frames = 0, dropped = 0;
    let rx = 0, healthTimer = null;   // kelgan kadrlar / dekoder ulguryaptimi
    // JITTER BUFER. Bulut kameralari kadrlarni TEKIS emas, GOP'ma-GOP yuboradi:
    // o'lchandi — 500 kadrdan 491 tasi <10ms oralig'ida keldi, keyin 2 SONIYA
    // jimlik (aynan keyframe oralig'i). Kadrni kelishi bilan chizsak, tasvir 2s
    // qotadi, keyin 50 kadr bir zumda "tez suratga" o'tadi — foydalanuvchi buni
    // "qotish" deb ko'radi. Telefondagi Hik-Connect silliq ko'rinishining sababi
    // ham shu: u buferlaydi. Shuning uchun kadrlarni DEKODLASH tez, CHIZISH esa
    // kadr timestamp'i bo'yicha o'lchovli bo'ladi.
    const pending = [];               // dekodlangan, chizilishini kutayotgan kadrlar
    const rxTimes = [];               // kadr kelish vaqtlari (tezlikni o'lchash uchun)
    let baseWall = 0;                 // oxirgi chizish vaqti (pace soati)
    let paceTimer = null;
    let firstTimer = setTimeout(() => fail(new Error("WS: birinchi kadr kelmadi")), FIRST_FRAME_TIMEOUT);
    // Qat'iy muddat — `wait` pulslari uni CHO'ZMAYDI.
    const hardTimer = maxWaitMs
      ? setTimeout(() => fail(new Error(`WS: ${Math.round(maxWaitMs / 1000)}s ichida kadr kelmadi`)), maxWaitMs)
      : null;

    const handle = {
      codec: "", w: 0, h: 0,
      close() {
        closed = true;
        clearTimeout(firstTimer);
        clearTimeout(healthTimer);
        clearInterval(healthTimer);   // oyna taymeri interval bo'lishi mumkin
        if (paceTimer) clearTimeout(paceTimer);
        while (pending.length) { try { pending.shift().close(); } catch { /* ignore */ } }
        if (hardTimer) clearTimeout(hardTimer);
        try { ws.close(); } catch { /* ignore */ }
        try { if (decoder && decoder.state !== "closed") decoder.close(); } catch { /* ignore */ }
      },
    };

    function fail(err) {
      if (settled) { if (onEnd && !closed) onEnd(err?.message || String(err)); handle.close(); return; }
      settled = true;
      handle.close();
      reject(err);
    }

    /** Dekoder chiqishi — darrov chizmaymiz, navbatga qo'yib O'LCHOVLI chizamiz. */
    function enqueue(frame) {
      // FONDA (vkladka yashirin / alt+tab) brauzer setTimeout'ni ~1 s ga sekinlashtiradi
      // (5 daqiqadan keyin 1 daqiqaga). Pacing taymerga bog'liq bo'lgani uchun chizish
      // deyarli to'xtardi, `onStats` pulsi kelmay qolardi va devor watchdog'i plitkani
      // qayta-qayta ulardi — foydalanuvchi "fonda qoldirsam kamera o'chib qolyapti" deb
      // ko'rardi. Yashirin holatda kadr KELISHI BILAN chiziladi (canvas ko'rinmaydi,
      // tekislik kerak emas); qaytganda pacing o'zi davom etadi.
      if (document.hidden) {
        while (pending.length) { try { pending.shift().close(); } catch { /* ignore */ } dropped += 1; }
        if (paceTimer) { clearTimeout(paceTimer); paceTimer = null; }
        baseWall = 0;
        draw(frame);
        return;
      }
      pending.push(frame);
      // Bufer haddan oshsa (manba sakradi yoki tab fonda edi) — eng ESKILARINI
      // tashlab, jonliga yaqinlashamiz. Hammasini tashlab yubormaymiz: bufer
      // bo'shab qolsa tasvir yana burst bo'lib chiziladi (o'lchandi: chizish
      // oraliqlari 40ms o'rniga 1ms bo'lib ketardi).
      if (pending.length > PACE_MAX_FRAMES) {
        while (pending.length > PACE_TRIM_FRAMES) {
          try { pending.shift().close(); } catch { /* ignore */ }
          dropped += 1;
        }
        baseWall = 0;   // qolgan navbat boshidan qayta bog'laymiz
      }
      pace();
    }

    /** Kadr timestamp'lari ishonchlimi (haqiqiy qadam bormi)? */
    function stepFromTimestamps() {
      if (pending.length < 2) return 0;
      const d = (pending[1].timestamp - pending[0].timestamp) / 1000;
      return d > 1 && d < 500 ? d : 0;    // 0 -> ishonchsiz (klasterlangan)
    }

    /**
     * Navbatdagi kadrni chizish uchun taymer qo'yadi.
     *
     * DIQQAT: kadr timestamp'lariga TAYANIB BO'LMAYDI. O'lchandi — bulut
     * publisher'ida `-use_wallclock_as_timestamps` bor, kadrlar esa bulutdan
     * GOP'ma-GOP (burst) keladi, shuning uchun bir burstdagi 50 kadr deyarli
     * BIR XIL timestamp oladi: 250 kadrdan 211 tasining qadami 0 ms. Shu sababli
     * chizish tezligi KELISH TEZLIGIDAN hisoblanadi (oxirgi 4s), bufer chuqurligi
     * esa tezlikni ±% moslaydi — klassik jitter-bufer. Timestamp ishonchli
     * bo'lsa (RTSP kameralar) o'sha ishlatiladi.
     */
    function pace() {
      if (paceTimer || closed || !pending.length) return;
      const now = performance.now();
      // Kelish tezligi -> nominal kadr qadami.
      while (rxTimes.length && now - rxTimes[0] > PACE_RATE_WINDOW_MS) rxTimes.shift();
      const fps = rxTimes.length > 5 ? (rxTimes.length * 1000) / PACE_RATE_WINDOW_MS : 0;
      const nominal = stepFromTimestamps() || (fps > 4 ? 1000 / fps : 40);
      // Bufer chuqurligi (ms). Chuqur bo'lsa biroz tezroq, sayoz bo'lsa sekinroq
      // chizamiz — shunda bufer maqsad atrofida turadi va tasvir tekis oqadi.
      const depth = pending.length * nominal;
      let step = nominal;
      if (depth > PACE_TARGET_MS * 2) step = nominal * 0.85;
      else if (depth < PACE_TARGET_MS * 0.5) step = nominal * 1.15;
      // BIRINCHI KADR DARROV chiziladi. Bufer to'lishini KUTMAYMIZ: `connectWs`
      // faqat birinchi kadr chizilganda resolve bo'ladi, ya'ni kutish plitkani
      // "ulanmadi" holatiga olib borardi (o'lchandi: 9 plitkadan 5 tasi aynan
      // maxWaitMs da yiqildi). Zaxira keyin o'zi to'planadi — bufer sayoz
      // bo'lganda yuqoridagi `step` biroz sekinlashtiradi.
      if (!baseWall) baseWall = now - step;
      const wait = Math.max(0, baseWall + step - now);
      paceTimer = setTimeout(() => {
        paceTimer = null;
        baseWall = performance.now();
        if (!closed && pending.length) draw(pending.shift());
        pace();
      }, wait);
    }

    function draw(frame) {
      if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
        canvas.width = frame.displayWidth;
        canvas.height = frame.displayHeight;
      }
      ctx.drawImage(frame, 0, 0);
      frame.close();
      frames += 1;
      if (!settled) {
        settled = true;
        clearTimeout(firstTimer);
        if (hardTimer) clearTimeout(hardTimer);
        handle.codec = codec;
        // DIQQAT: `frame.close()` yuqorida chaqirilgan — VideoFrame uzilgan va
        // uning displayWidth/Height endi 0 qaytaradi. Canvas o'lchami esa aynan
        // shu kadrdan qo'yilgan, shuning uchun undan olamiz.
        handle.w = canvas.width; handle.h = canvas.height;
        // Birinchi kadrdan boshlab dekoder ULGURYAPTIMI — o'lchay boshlaymiz.
        if (onUnhealthy) {
          let strikes = 0, rx0 = 0, out0 = 0;
          healthTimer = setTimeout(() => {
            rx0 = rx; out0 = frames;                     // tinchlanishdan KEYIN sanaymiz
            healthTimer = setInterval(() => {
              const got = rx - rx0, drew = frames - out0;
              rx0 = rx; out0 = frames;
              if (got > 20 && drew / got < HEALTH_MIN_RATIO) {
                strikes += 1;
                if (strikes >= HEALTH_STRIKES) {
                  clearInterval(healthTimer);
                  onUnhealthy({ received: got, decoded: drew });
                }
              } else {
                strikes = 0;                             // bitta sakrash muammo emas
              }
            }, HEALTH_WINDOW_MS);
          }, HEALTH_SETTLE_MS);
        }
        resolve(handle);
      }
      if (onStats && frames % 30 === 0) onStats({ frames, dropped, queue: decoder?.decodeQueueSize ?? 0 });
    }

    async function init(msg) {
      codec = msg.codec;
      const cfg = { codec: msg.codecString, optimizeForLatency: true };
      let sup = null;
      try { sup = await VideoDecoder.isConfigSupported(cfg); } catch (e) { fail(e); return; }
      if (!sup?.supported) { fail(new Error(`WebCodecs ${msg.codecString} qo'llanmaydi`)); return; }
      if (decoder) { try { decoder.close(); } catch { /* ignore */ } }
      decoder = new VideoDecoder({
        output: enqueue,
        error: (e) => fail(e),
      });
      decoder.configure(cfg);
      needKey = true;
    }

    ws.onmessage = (ev) => {
      if (closed) return;
      if (typeof ev.data === "string") {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.type === "init") init(msg);
        else if (msg.type === "end") {
          const err = new Error(msg.reason || "oqim tugadi");
          // Manba (kamera) aybdor bo'lsa WebRTC/HLS ham ishlamaydi — chaqiruvchi
          // fallback qilmasdan aniq xabar ko'rsatadi.
          err.sourceFailed = !!msg.source_failed;
          fail(err);
        }
        else if (msg.type === "wait" && !settled) {
          // Backend manbani (publisher) kutyapti — bizning taymer ham kutsin.
          clearTimeout(firstTimer);
          firstTimer = setTimeout(() => fail(new Error("WS: birinchi kadr kelmadi")), FIRST_FRAME_TIMEOUT);
          if (onWait) onWait(msg.waited);
        }
        return;
      }
      if (!decoder || decoder.state !== "configured") return;
      const buf = ev.data;
      if (buf.byteLength <= HDR) return;
      rx += 1;
      rxTimes.push(performance.now());   // kelish tezligi (pace uchun)
      const dv = new DataView(buf);
      const key = (dv.getUint8(0) & 1) === 1;
      const ts = Number(dv.getBigUint64(4));
      // Orqada qolsak (sekin GPU/CPU) — jonliga sakrash: kalit kadrgacha tashlaymiz.
      if (decoder.decodeQueueSize > MAX_QUEUE && !key) { needKey = true; dropped += 1; return; }
      if (needKey && !key) return;
      needKey = false;
      try {
        decoder.decode(new EncodedVideoChunk({
          type: key ? "key" : "delta", timestamp: ts, data: new Uint8Array(buf, HDR),
        }));
      } catch (e) {
        // Dekoder buzildi (masalan, mos kelmagan kadr) — kalitdan qayta boshlaymiz
        needKey = true;
        if (decoder.state === "closed") fail(e);
      }
    };
    ws.onerror = () => fail(new Error("WS ulanish xatosi"));
    ws.onclose = (ev) => { if (!closed) fail(new Error(`WS yopildi (${ev.code})`)); };
  });
}
