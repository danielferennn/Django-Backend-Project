import secrets

from django.conf import settings
from rest_framework.exceptions import PermissionDenied


def require_device_token(request) -> None:
    expected = settings.SMARTLOCKER_DEVICE_TOKEN
    provided = request.headers.get('X-Device-Token', '')
    try:
        authorized = secrets.compare_digest(provided, expected)
    except (TypeError, UnicodeEncodeError):
        authorized = False
    if not authorized:
        raise PermissionDenied('device_auth_failed')
