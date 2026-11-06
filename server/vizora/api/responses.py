"""Panel va iste'molchi API'lari uchun UMUMIY javob yordamchilari.

Ruxsat (admin rol / ApiClient scope) har router'ning o'zida tekshiriladi; bu yerga
faqat tekshiruvdan o'tgan kamera keladi."""
from fastapi import HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.models.camera import Camera
from vizora.services import camera_streams, snapshot


async def snapshot_response(db: AsyncSession, cam: Camera, *, fresh: bool) -> Response:
    """Kameraning oxirgi keshlangan JPEG kadri (grid/ro'yxat "poster"i).

    Kesh DARROV qaytadi (CPU ~0); eskirgan bo'lsa FONDA yangilanadi (bloklamaydi).
    **200** JPEG · **202** rasm hali yo'q (mijoz biroz keyin qayta so'raydi) ·
    **404** kamerada snapshot manbai yo'q (hisobsiz EZVIZ / URL'siz RTSP)."""
    if not camera_streams.has_snapshot_source(cam):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bu kamerada snapshot manbai yo'q")
    cached = snapshot.get_cached(str(cam.id))
    await snapshot.start_refresh(db, cam, fresh=fresh)
    if cached:
        return Response(cached, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    return Response(status_code=status.HTTP_202_ACCEPTED)
