"""RTSP/RTP o'quvchi — SDP, RTP sarlavha, depacketizatsiya, AU yig'ish, codec satri."""
import base64

import pytest

from vizora.media import rtsp_reader as rr


# ---------------------------------------------------------------- yordamchilar

def rtp(payload: bytes, *, seq: int, ts: int, marker: bool = False, pt: int = 96) -> bytes:
    b0 = 0x80
    b1 = (0x80 if marker else 0) | pt
    return bytes([b0, b1]) + seq.to_bytes(2, "big") + ts.to_bytes(4, "big") + b"\x00\x00\x00\x01" + payload


SPS264 = bytes([0x67, 0x64, 0x00, 0x1F, 0xAC, 0xD9, 0x40, 0x50])   # High 3.1
PPS264 = bytes([0x68, 0xEB, 0xE3, 0xCB, 0x22, 0xC0])
IDR264 = bytes([0x65, 0x88, 0x84, 0x00, 0x33, 0xFF])
P264 = bytes([0x41, 0x9A, 0x22, 0x0C])

# HEVC Main: NAL sarlavha(2) + vps_id/sub_layers(1) + PTL(12: ptl[0]=0x01 Main, compat 0x60000000, constraint B0.., level 93)
SPS265 = bytes([0x42, 0x01, 0x01, 0x01, 0x60, 0x00, 0x00, 0x00, 0xB0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x5D, 0xA0])
VPS265 = bytes([0x40, 0x01, 0x0C, 0x01])
PPS265 = bytes([0x44, 0x01, 0xC1])
IDR265 = bytes([0x26, 0x01, 0xAF, 0x00])   # type 19 (IDR_W_RADL)
P265 = bytes([0x02, 0x01, 0xD0, 0x00])     # type 1 (TRAIL_R)


def h264_track() -> rr.Track:
    return rr.Track(codec=rr.H264, payload_type=96, control="rtsp://x/cam/1/trackID=0")


def h265_track() -> rr.Track:
    return rr.Track(codec=rr.H265, payload_type=96, control="rtsp://x/cam/1/trackID=0")


# ---------------------------------------------------------------- SDP

def test_parse_sdp_h264_sprop_and_relative_control():
    sdp = (
        "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=x\r\n"
        "m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
        f"a=fmtp:96 packetization-mode=1; sprop-parameter-sets={base64.b64encode(SPS264).decode()},{base64.b64encode(PPS264).decode()}; profile-level-id=64001F\r\n"
        "a=control:trackID=0\r\n"
    )
    t = rr.parse_sdp(sdp, "rtsp://mediamtx:8554/cam/abc/")
    assert t.codec == rr.H264 and t.payload_type == 96 and t.clock == 90000
    assert t.control == "rtsp://mediamtx:8554/cam/abc/trackID=0"
    assert t.sps == SPS264 and t.pps == PPS264


def test_parse_sdp_h265_sprop_absolute_control_ignores_audio():
    sdp = (
        "v=0\r\nm=audio 0 RTP/AVP 97\r\na=rtpmap:97 MPEG4-GENERIC/48000\r\na=control:trackID=1\r\n"
        "m=video 0 RTP/AVP 96\r\na=rtpmap:96 H265/90000\r\n"
        f"a=fmtp:96 sprop-vps={base64.b64encode(VPS265).decode()}; sprop-sps={base64.b64encode(SPS265).decode()}; sprop-pps={base64.b64encode(PPS265).decode()}\r\n"
        "a=control:rtsp://mediamtx:8554/cam/abc/trackID=0\r\n"
    )
    t = rr.parse_sdp(sdp, "rtsp://mediamtx:8554/cam/abc/")
    assert t.codec == rr.H265
    assert t.control == "rtsp://mediamtx:8554/cam/abc/trackID=0"
    assert (t.vps, t.sps, t.pps) == (VPS265, SPS265, PPS265)


def test_parse_sdp_without_video_raises():
    with pytest.raises(rr.RtspError):
        rr.parse_sdp("v=0\r\nm=audio 0 RTP/AVP 97\r\na=rtpmap:97 opus/48000\r\n", "rtsp://x/")


# ---------------------------------------------------------------- RTP sarlavha

def test_parse_rtp_basic_and_padding_extension():
    p = rr.parse_rtp(rtp(b"\x65\x01\x02", seq=7, ts=9000, marker=True))
    assert p and p.marker and p.seq == 7 and p.timestamp == 9000 and p.payload == b"\x65\x01\x02"
    # extension (1 so'z) + padding (2 bayt)
    hdr = bytes([0x80 | 0x10 | 0x20, 96]) + (1).to_bytes(2, "big") + (0).to_bytes(4, "big") + b"\x00\x00\x00\x01"
    ext = b"\xBE\xDE" + (1).to_bytes(2, "big") + b"\x00\x00\x00\x00"
    data = hdr + ext + b"\x41\xAA" + b"\x00\x02"
    p = rr.parse_rtp(data)
    assert p and p.payload == b"\x41\xAA"
    assert rr.parse_rtp(b"\x00" * 11) is None
    assert rr.parse_rtp(b"\x00" * 12) is None  # versiya 0


# ---------------------------------------------------------------- H.264 depacketizatsiya

def test_h264_single_stap_fu_and_access_units():
    d = rr.Depacketizer(h264_track())
    # AU 1: STAP-A(SPS,PPS) + FU-A(IDR ikkiga bo'lingan), marker oxirida
    stap = bytes([24]) + len(SPS264).to_bytes(2, "big") + SPS264 + len(PPS264).to_bytes(2, "big") + PPS264
    assert d.feed(rr.parse_rtp(rtp(stap, seq=1, ts=0))) == []
    fu1 = bytes([0x7C, 0x80 | 0x05]) + IDR264[1:4]          # S=1, type 5
    fu2 = bytes([0x7C, 0x40 | 0x05]) + IDR264[4:]           # E=1
    assert d.feed(rr.parse_rtp(rtp(fu1, seq=2, ts=0))) == []
    aus = d.feed(rr.parse_rtp(rtp(fu2, seq=3, ts=0, marker=True)))
    assert len(aus) == 1
    au = aus[0]
    assert au.key and au.ts_us == 0 and au.codec == rr.H264
    assert au.nals == [SPS264, PPS264, IDR264]
    # SDP'da bo'lmagan param-setlar oqimdan o'rganildi
    assert d.track.sps == SPS264 and d.track.pps == PPS264
    # AU 2: bitta P kadr, 3600 tick keyin (40ms @ 90kHz)
    aus = d.feed(rr.parse_rtp(rtp(P264, seq=4, ts=3600, marker=True)))
    assert len(aus) == 1 and not aus[0].key and aus[0].ts_us == 40_000 and aus[0].nals == [P264]


def test_h264_timestamp_change_flushes_without_marker():
    d = rr.Depacketizer(h264_track())
    d.feed(rr.parse_rtp(rtp(IDR264, seq=1, ts=0)))              # marker yo'q
    aus = d.feed(rr.parse_rtp(rtp(P264, seq=2, ts=3600)))       # ts o'zgardi -> avvalgi AU chiqadi
    assert len(aus) == 1 and aus[0].nals == [IDR264] and aus[0].key


def test_h264_fu_with_lost_packet_is_dropped():
    d = rr.Depacketizer(h264_track())
    fu1 = bytes([0x7C, 0x85]) + b"\x01\x02"
    fu3 = bytes([0x7C, 0x45]) + b"\x05\x06"   # seq sakradi (2 yo'q)
    d.feed(rr.parse_rtp(rtp(fu1, seq=1, ts=0)))
    aus = d.feed(rr.parse_rtp(rtp(fu3, seq=3, ts=0, marker=True)))
    assert aus == []   # buzuq NAL chiqarilmadi, AU bo'sh — flush yo'q


def test_h264_ignores_other_payload_type():
    d = rr.Depacketizer(h264_track())
    assert d.feed(rr.parse_rtp(rtp(IDR264, seq=1, ts=0, marker=True, pt=97))) == []


# ---------------------------------------------------------------- H.265 depacketizatsiya

def test_h265_ap_fu_and_keyframe():
    d = rr.Depacketizer(h265_track())
    ap = bytes([48 << 1, 0x01])
    for n in (VPS265, SPS265, PPS265):
        ap += len(n).to_bytes(2, "big") + n
    assert d.feed(rr.parse_rtp(rtp(ap, seq=1, ts=0))) == []
    # FU: PayloadHdr(type 49) + FU header (S/E + type 19)
    fu1 = bytes([49 << 1, 0x01, 0x80 | 19]) + IDR265[2:3]
    fu2 = bytes([49 << 1, 0x01, 0x40 | 19]) + IDR265[3:]
    d.feed(rr.parse_rtp(rtp(fu1, seq=2, ts=0)))
    aus = d.feed(rr.parse_rtp(rtp(fu2, seq=3, ts=0, marker=True)))
    assert len(aus) == 1 and aus[0].key
    assert aus[0].nals == [VPS265, SPS265, PPS265, IDR265]
    assert (d.track.vps, d.track.sps, d.track.pps) == (VPS265, SPS265, PPS265)
    aus = d.feed(rr.parse_rtp(rtp(P265, seq=4, ts=9000, marker=True)))
    assert len(aus) == 1 and not aus[0].key and aus[0].ts_us == 100_000


# ---------------------------------------------------------------- Annex-B va param-set qo'shish

def test_annexb_prepends_params_only_when_missing():
    au = rr.AccessUnit(nals=[IDR264], ts_us=0, key=True, codec=rr.H264)
    out = au.annexb((SPS264, PPS264))
    assert out == b"\x00\x00\x00\x01" + SPS264 + b"\x00\x00\x00\x01" + PPS264 + b"\x00\x00\x00\x01" + IDR264
    au2 = rr.AccessUnit(nals=[SPS264, PPS264, IDR264], ts_us=0, key=True, codec=rr.H264)
    assert au2.annexb((SPS264, PPS264)).count(SPS264) == 1


# ---------------------------------------------------------------- codec satri

def test_codec_string_h264_from_sps():
    assert rr.codec_string(rr.H264, SPS264) == "avc1.64001F"
    assert rr.codec_string(rr.H264, b"\x67") == "avc1.42E01E"


def test_codec_string_h265_main():
    # Main (idc 1), compat 0x60000000 -> teskari 0x60000000 -> "6", L93, cheklov B0
    assert rr.codec_string(rr.H265, SPS265) == "hev1.1.6.L93.B0"


# ---------------------------------------------------------------- timestamp wrap

def test_timestamp_wraparound_is_monotonic():
    d = rr.Depacketizer(h264_track())
    start = 0xFFFF_FFFF - 1800
    a1 = d.feed(rr.parse_rtp(rtp(IDR264, seq=1, ts=start, marker=True)))[0]
    a2 = d.feed(rr.parse_rtp(rtp(P264, seq=2, ts=(start + 3600) & 0xFFFF_FFFF, marker=True)))[0]
    assert a1.ts_us == 0 and a2.ts_us == 40_000


# ---------------------------------------------------------------- emulation prevention

def test_rbsp_strips_emulation_prevention():
    assert rr.rbsp(b"\x00\x00\x03\x01") == b"\x00\x00\x01"
    assert rr.rbsp(b"\x00\x00\x03\x00\x00\x03\x02") == b"\x00\x00\x00\x00\x02"
    assert rr.rbsp(b"\xAA\xBB") == b"\xAA\xBB"          # tegishsiz baytlar o'zgarmaydi
    assert rr.rbsp(b"\x00\x00\x04") == b"\x00\x00\x04"  # 03 emas — qistirma emas


def test_codec_string_hevc_with_emulation_bytes():
    """Haqiqiy kamerada (3200x1800) SPS ichida 0x03 qistirmalari bor edi va ularni
    olib tashlamaslik `L0` (daraja 0) beradi — brauzer dekoderni noto'g'ri sozlab
    1412 kadrdan 177 tasini chizgan."""
    # SPS265 ning PTL qismida `00 00` bor (5,6-indeks) — enkoder o'sha yerga 0x03
    # qistiradi. Shu holatni takrorlaymiz.
    dirty = SPS265[:7] + b"\x03" + SPS265[7:]
    assert dirty[5:8] == b"\x00\x00\x03"            # qistirma haqiqatan shu yerda
    assert rr.codec_string(rr.H265, dirty) == rr.codec_string(rr.H265, SPS265)
    assert rr.codec_string(rr.H265, dirty) == "hev1.1.6.L93.B0"
    assert ".L0." not in rr.codec_string(rr.H265, dirty)


def test_codec_string_h264_with_emulation_bytes():
    dirty = b"\x67\x00\x00\x03\x1F" + SPS264[4:]
    assert rr.codec_string(rr.H264, dirty) == "avc1.00001F"
