"""EZVIZ bulut kameralarini integratsiya qilish.

EZVIZ 2023'dan RTSP'ni o'chirgan — to'g'ridan RTSP ishlamaydi. Buning o'rniga
`pyezvizapi` orqali bulutga login qilib (Android client = terminal-limit chetlab),
kameraning VTM cloud oqimi tortiladi. Oddiy kameralar uchun MPEG-TS, shifrlangan
(batareyali) kameralar uchun esa verification code bilan dekodlangan HEVC olinadi.

Shifrlangan (batareyali) kameralar uchun NAL dekodlash pyezvizapi'ning yetuk
`decrypt_hikvision_ps_video`'si orqali bajariladi (verification code AES kalit
sifatida) — MFA/encryptkey kerak emas. Bu NAL header o'lchamini avto-aniqlaydi
(ba'zi firmware NAL header'ini ham shifrlaydi).

Modullar:
  * client.py   — EZVIZ bulutiga login (terminal-limit chetlab) + qurilmalar ro'yxati
  * tokens.py   — filial bo'yicha sessiya token'larini saqlash/yangilash
  * publish.py  — bulut oqimini dekodlab MediaMTX'ga RTSP publish qiluvchi jarayon
  * manager.py  — on-demand supervisor: tomoshabin bo'lganda publisher'ni boshqaradi
"""
