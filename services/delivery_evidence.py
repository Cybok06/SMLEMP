"""Validate camera snapshots and upload them through the existing Images integration."""
import io
import warnings
from datetime import datetime

from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

MAX_PHOTO_BYTES = 8 * 1024 * 1024


def upload_delivery_photo(photo, identity):
    from Backend.inventory.stock_deductions_store import StockDeductionError

    if not photo or not photo.filename:
        raise StockDeductionError('Take a delivery photo with the camera before confirming delivery.', 'delivery_photo_required')
    raw = photo.stream.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise StockDeductionError('The delivery photo must be smaller than 8 MB.', 'delivery_photo_too_large', 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'JPEG':
                    raise ValueError('A camera JPEG snapshot is required.')
                if image.width * image.height > 16_000_000:
                    raise ValueError('Snapshot dimensions are too large.')
                image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise StockDeductionError('The delivery photo is invalid. Take a new photo using the camera.', 'invalid_delivery_photo')

    from product_profile import upload_to_cloudflare
    snapshot = FileStorage(stream=io.BytesIO(raw), filename='delivery-camera.jpg', content_type='image/jpeg')
    ok, uploaded = upload_to_cloudflare(snapshot, source='agent_delivery_evidence')
    if not ok or not uploaded.get('image_id') or not uploaded.get('url'):
        raise StockDeductionError('The delivery photo could not be uploaded. Please retry; the product has not been marked delivered.', 'delivery_photo_upload_failed', 502)
    return {
        'image_id': uploaded['image_id'], 'url': uploaded['url'],
        'provider': 'cloudflare_images', 'source': 'camera',
        'uploaded_at': datetime.utcnow(), 'uploaded_by': str(identity.get('user_id') or ''),
    }
