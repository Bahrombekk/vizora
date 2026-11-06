"""Iste'molchi (API mijoz) ruxsatini tekshirish — WS va REST uchun umumiy.

Moslashuvchan scope (birortasi mos kelsa ruxsat beriladi):
  {"all": true}                   -> barcha kameralar
  {"branches": ["<id>", ...]}  -> shu filiallar (ostidagi hududlar) kameralari
  {"zones":   ["<id>", ...]}  -> shu hududlar kameralari
  {"cameras":     ["<id>", ...]}  -> aniq tanlangan kameralar
Filial/hudud ixtiyoriy bo'lgani uchun kamera biriktirilmagan bo'lsa (zone=None),
faqat "all" yoki "cameras" orqali ruxsat beriladi.
"""
from vizora.models.camera import Camera
from vizora.models.client import ApiClient
from vizora.models.zone import Zone


def _id_list(value: object) -> list[str]:
    """Scope ro'yxatini xavfsiz normallashtiradi — list bo'lmasa bo'sh.

    Bu, masalan, `{"cameras": "<uuid>"}` (string) substring-moslik orqali
    kutilmagan kirish berishining oldini oladi."""
    return [str(x) for x in value] if isinstance(value, list) else []


def _is_all(value: object) -> bool:
    """`all` faqat haqiqiy `true` bo'lsa — string "false" TRUTHY bo'lib qolmasin."""
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def can_access(client: ApiClient, camera: Camera, zone: Zone | None) -> bool:
    """`client` shu `camera`ni ko'ra oladimi (scope bo'yicha)."""
    sc = client.scopes
    if not isinstance(sc, dict):
        return False
    if _is_all(sc.get("all")):
        return True
    if str(camera.id) in _id_list(sc.get("cameras")):
        return True
    if camera.zone_id and str(camera.zone_id) in _id_list(sc.get("zones")):
        return True
    if zone is not None and str(zone.branch_id) in _id_list(sc.get("branches")):
        return True
    return False
