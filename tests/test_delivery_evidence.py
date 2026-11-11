import io
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from Backend.inventory.stock_deductions_store import StockDeductionError
from services.delivery_evidence import MAX_PHOTO_BYTES, upload_delivery_photo


def camera_photo(format='JPEG'):
    stream = io.BytesIO()
    Image.new('RGB', (100, 100), 'blue').save(stream, format=format)
    stream.seek(0)
    return FileStorage(stream=stream, filename='snapshot.jpg', content_type='image/jpeg')


@pytest.mark.parametrize('photo,code', [
    (None, 'delivery_photo_required'),
    (FileStorage(stream=io.BytesIO(b'video or fake image'), filename='fake.jpg'), 'invalid_delivery_photo'),
    (camera_photo('PNG'), 'invalid_delivery_photo'),
    (FileStorage(stream=io.BytesIO(b'x' * (MAX_PHOTO_BYTES + 1)), filename='big.jpg'), 'delivery_photo_too_large'),
])
def test_missing_invalid_and_oversized_photos_never_upload(photo, code):
    uploader = Mock()
    with patch.dict(sys.modules, {'product_profile': SimpleNamespace(upload_to_cloudflare=uploader)}):
        with pytest.raises(StockDeductionError) as error:
            upload_delivery_photo(photo, {'user_id': 'agent'})
    assert error.value.code == code
    uploader.assert_not_called()


def test_cloudflare_failure_blocks_delivery():
    uploader = Mock(return_value=(False, {'stage': 'network'}))
    with patch.dict(sys.modules, {'product_profile': SimpleNamespace(upload_to_cloudflare=uploader)}):
        with pytest.raises(StockDeductionError) as error:
            upload_delivery_photo(camera_photo(), {'user_id': 'agent'})
    assert error.value.code == 'delivery_photo_upload_failed'
    assert error.value.status == 502


def test_photo_uses_existing_cloudflare_uploader_and_records_server_metadata():
    uploader = Mock(return_value=(True, {'image_id': 'cf-photo', 'url': 'https://imagedelivery.net/account/cf-photo/public'}))
    with patch.dict(sys.modules, {'product_profile': SimpleNamespace(upload_to_cloudflare=uploader)}):
        evidence = upload_delivery_photo(camera_photo(), {'user_id': 'agent'})
    assert evidence['image_id'] == 'cf-photo'
    assert evidence['uploaded_by'] == 'agent'
    assert evidence['source'] == 'camera'
    assert evidence['provider'] == 'cloudflare_images'
    assert evidence['uploaded_at']
    assert uploader.call_args.kwargs == {'source': 'agent_delivery_evidence'}
    sent = uploader.call_args.args[0]
    assert sent.filename == 'delivery-camera.jpg'
    with Image.open(sent.stream) as image:
        assert image.format == 'JPEG'
