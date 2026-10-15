"""Face-enrolment and recognition services.

Training images live in private storage and the trained LBPH model is rebuilt
from the *current* private training records on every enrolment/removal.  The
model is never read from a checked-in artifact, so a stale label can never be
served once its training data (or owner) is gone.
"""

import fcntl
import logging
import os
import stat
import uuid
import warnings

try:
    import cv2
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - exercised only without OpenCV
    cv2 = None
    np = None

try:
    from PIL import Image, UnidentifiedImageError
except ModuleNotFoundError:  # pragma: no cover - Pillow is a hard dependency
    Image = None
    UnidentifiedImageError = None

from django.conf import settings

from apps.users.models import User

from .models import Datawajahnew

logger = logging.getLogger(__name__)

MAX_FACE_IMAGE_BYTES = 5 * 1024 * 1024

# A small compressed upload can still declare an enormous canvas (a PNG
# "decompression bomb").  Bound the decoded canvas *before* handing any bytes
# to OpenCV so a tiny request cannot force a huge allocation.  4096 per side
# accepts any realistic camera frame, and the ~12MP pixel cap rejects a
# square 4096x4096 canvas even though each side is within the dimension limit.
MAX_IMAGE_DIMENSION = 4096
MAX_IMAGE_PIXELS = 12_000_000

FACE_CASCADE_PATH = settings.BASE_DIR / 'apps' / 'hasiltraining' / 'haarcascade_frontalface_default.xml'
FACE_MODEL_PATH = settings.PRIVATE_MEDIA_ROOT / 'biometrics' / 'lbph_model.xml'

# Detection/training parameters shared by validation and rebuild so a stored
# image is judged by exactly the same rule as a fresh upload.
DETECT_SCALE_FACTOR = 1.2
DETECT_MIN_NEIGHBORS = 5
DETECT_MIN_SIZE = (50, 50)

# Biometric artifacts are sensitive: keep the model directory and file private
# to the service account (and, for the directory, its group) instead of the
# world/group-readable defaults that raw ``mkdir``/OpenCV ``save`` would create.
MODEL_DIR_MODE = 0o750
MODEL_FILE_MODE = 0o640
MODEL_LOCK_MODE = 0o600

class FaceRecognitionUnavailable(Exception):
    """Raised when the optional OpenCV dependencies are missing or unusable."""

class InvalidFaceImage(Exception):
    """Raised when an upload (or stored image) is not a single usable face."""

class ModelPermissionError(FaceRecognitionUnavailable):
    """Raised when biometric files cannot be tightened to private permissions.

    Subclasses :class:`FaceRecognitionUnavailable` so it maps to 503: refusing
    to train is the fail-closed behaviour, since a world-readable model would
    leak biometric data.
    """

def _require_dependencies():
    if cv2 is None or np is None or not hasattr(cv2, 'face'):
        raise FaceRecognitionUnavailable('OpenCV face-recognition dependencies are unavailable.')

def _cascade():
    _require_dependencies()
    cascade = cv2.CascadeClassifier(str(FACE_CASCADE_PATH))
    if cascade.empty():
        raise FaceRecognitionUnavailable('Face detection classifier is unavailable.')
    return cascade

def _detect_single_face(gray_image, cascade):
    faces = cascade.detectMultiScale(
        gray_image,
        scaleFactor=DETECT_SCALE_FACTOR,
        minNeighbors=DETECT_MIN_NEIGHBORS,
        minSize=DETECT_MIN_SIZE,
    )
    if len(faces) != 1:
        return None
    x, y, width, height = faces[0]
    return gray_image[y:y + height, x:x + width]

def _guard_dimensions(width, height):
    """Reject decoded canvases that are absurdly large or would blow memory."""
    if width <= 0 or height <= 0:
        raise InvalidFaceImage('Upload must be a decodable image.')
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS:
        raise InvalidFaceImage(
            f'Image dimensions must not exceed {MAX_IMAGE_DIMENSION}x{MAX_IMAGE_DIMENSION}.'
        )

def _check_dimensions(image_file):
    """Bound an upload's declared canvas before OpenCV allocates any buffer.

    Pillow parses only the image header here, so a tiny "decompression bomb"
    (e.g. a 68-byte PNG declaring 50000x50000) is rejected without decoding.
    The stream is reset afterwards so the caller can still read the bytes.
    """
    _require_dependencies()
    if Image is None:
        raise FaceRecognitionUnavailable('Image decoding dependencies are unavailable.')
    image_file.seek(0)
    try:
        with warnings.catch_warnings():
            # Pillow warns *and* raises DecompressionBombError past 2x the
            # limit; silence the warning so the explicit guard below decides.
            warnings.simplefilter('ignore', Image.DecompressionBombWarning)
            with Image.open(image_file) as image:
                width, height = image.size
    except Image.DecompressionBombError as exc:
        raise InvalidFaceImage('Image dimensions are too large to process safely.') from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidFaceImage('Upload must be a decodable image.') from exc
    finally:
        image_file.seek(0)
    _guard_dimensions(width, height)

def decode_camera_image(image_file):
    """Decode a device camera upload into a BGR image."""
    if image_file.size is None or image_file.size > MAX_FACE_IMAGE_BYTES:
        raise InvalidFaceImage(f'Image must not exceed {MAX_FACE_IMAGE_BYTES // (1024 * 1024)} MiB.')
    _check_dimensions(image_file)
    _require_dependencies()
    encoded = image_file.read()
    image_file.seek(0)
    decoded = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise InvalidFaceImage('Upload must be a decodable image.')
    return decoded

def validate_face_image(image_file):
    """Validate an enrolment upload: size, dimensions, decodability, one face.

    The size and dimension checks deliberately run before OpenCV decodes the
    bytes so an oversize or bomb-like upload is rejected without allocating a
    large buffer.
    """
    if image_file.size is None or image_file.size > MAX_FACE_IMAGE_BYTES:
        raise InvalidFaceImage(f'Image must not exceed {MAX_FACE_IMAGE_BYTES // (1024 * 1024)} MiB.')
    _check_dimensions(image_file)
    _require_dependencies()
    encoded = image_file.read()
    image_file.seek(0)
    decoded = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        raise InvalidFaceImage('Upload must be a decodable image.')
    if _detect_single_face(decoded, _cascade()) is None:
        raise InvalidFaceImage('Each training image must contain exactly one detectable face.')

def _training_records():
    """Training records that are eligible to contribute a model label."""
    return (
        Datawajahnew.objects.select_related('user')
        .filter(
            image_user__isnull=False,
            user__role=User.Role.OWNER,
            user__is_active=True,
        )
        .exclude(image_user='')
        .order_by('pk')
    )

def _load_face_sample(record, cascade):
    """Return the cropped face for a stored record, or ``None`` if unusable.

    A stored record predating the upload guard (or tampered with on disk) must
    not force a huge allocation during a rebuild, so the stored byte size and
    the *declared* header dimensions are bounded before any bytes are read or
    handed to OpenCV.  Unusable records are skipped, never fatal.
    """
    try:
        size = record.image_user.size
    except (FileNotFoundError, OSError):
        return None
    if size is None or size > MAX_FACE_IMAGE_BYTES:
        logger.warning('Skipping oversized stored face training record %s.', record.pk)
        return None
    try:
        with record.image_user.open('rb') as source:
            try:
                _check_dimensions(source)
            except InvalidFaceImage:
                logger.warning('Skipping unprocessable stored face training record %s.', record.pk)
                return None
            source.seek(0)
            encoded = np.frombuffer(source.read(), dtype=np.uint8)
    except (FileNotFoundError, OSError):
        return None
    image = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    return _detect_single_face(image, cascade)

def _train_recognizer(samples, labels):
    _require_dependencies()
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(samples, np.asarray(labels, dtype=np.int32))
    return recognizer

def _remove_model():
    FACE_MODEL_PATH.unlink(missing_ok=True)

def _chmod(path, mode):
    """Tighten ``path`` to ``mode``, failing closed if it cannot be verified.

    A biometric artifact left world/group-readable is a data leak, so a failure
    to apply (or verify) the mode raises :class:`ModelPermissionError` (a 503)
    instead of being silently ignored.  The offending path is logged internally
    and deliberately kept out of the exception message, which is returned to
    API callers via ``str(exc)``.
    """
    try:
        os.chmod(path, mode)
    except OSError as exc:
        logger.error('Could not restrict permissions on biometric path %s.', path)
        raise ModelPermissionError(
            'Biometric storage permissions could not be secured.'
        ) from exc
    _verify_private_mode(path, mode)

def _verify_private_mode(path, mode):
    """Confirm ``path`` has no bits beyond ``mode`` (defence in depth)."""
    try:
        actual = stat.S_IMODE(os.stat(path).st_mode)
    except OSError as exc:
        logger.error('Could not verify permissions on biometric path %s.', path)
        raise ModelPermissionError(
            'Biometric storage permissions could not be verified.'
        ) from exc
    if actual & ~mode:
        logger.error(
            'Biometric path %s is not private (mode %o exceeds %o).', path, actual, mode,
        )
        raise ModelPermissionError('Biometric storage permissions are not private.')

def _ensure_model_directory():
    """Create the model directory private to the service account.

    ``mkdir`` honours the process umask (commonly 0755), so the mode is applied
    explicitly and re-applied on every rebuild to also tighten a directory that
    a previous version created with looser permissions.
    """
    directory = FACE_MODEL_PATH.parent
    directory.mkdir(parents=True, exist_ok=True, mode=MODEL_DIR_MODE)
    _chmod(directory, MODEL_DIR_MODE)

def _save_model_atomically(recognizer):
    """Write the model to a sibling temp file then atomically replace it.

    The temp file is chmod'd to ``MODEL_FILE_MODE`` *before* the atomic
    ``os.replace`` so the final model never exists with world/group-readable
    permissions, even transiently.
    """
    temporary_path = FACE_MODEL_PATH.with_name(f'.{FACE_MODEL_PATH.name}.{uuid.uuid4().hex}.tmp.xml')
    try:
        recognizer.save(str(temporary_path))
        _chmod(temporary_path, MODEL_FILE_MODE)
        os.replace(temporary_path, FACE_MODEL_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)
    _chmod(FACE_MODEL_PATH, MODEL_FILE_MODE)

def rebuild_face_model():
    """Rebuild the LBPH model from current private training records.

    Concurrency is serialised with an advisory file lock and the model is
    replaced atomically, so a reader never observes a half-written model.  When
    no usable training record remains the stale model is removed.  Returns the
    number of samples used.
    """
    _ensure_model_directory()
    lock_path = FACE_MODEL_PATH.with_name(f'{FACE_MODEL_PATH.name}.lock')
    with lock_path.open('a+b') as lock_file:
        _chmod(lock_path, MODEL_LOCK_MODE)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            records = list(_training_records())
            if not records:
                _remove_model()
                return 0

            _require_dependencies()
            cascade = _cascade()
            samples = []
            labels = []
            for record in records:
                face_id = record.user.face_id
                if not face_id or not face_id.isdigit():
                    continue
                sample = _load_face_sample(record, cascade)
                if sample is None:
                    logger.warning('Skipping unusable face training record %s.', record.pk)
                    continue
                samples.append(sample)
                labels.append(int(face_id))

            if not samples:
                _remove_model()
                return 0

            recognizer = _train_recognizer(samples, labels)
            _save_model_atomically(recognizer)
            return len(samples)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

def reconcile_face_model_for_user(user):
    """Rebuild or drop the model after a user's role/active state changed.

    Called from the user lifecycle so a downgraded or deactivated owner does not
    leave a stale, still-labelled biometric model on disk.  Returns the number
    of samples used (``0`` when the model was removed).
    """
    return rebuild_face_model()

def capture_training_file_names(user):
    """Return a user's stored training file names *before* the rows are deleted.

    Deleting a ``User`` cascades the ``Datawajahnew`` rows, so the names must be
    collected first; the bytes themselves are not removed by the cascade.
    """
    return list(
        Datawajahnew.objects.filter(user=user)
        .exclude(image_user='')
        .values_list('image_user', flat=True)
    )

def purge_training_files(names):
    """Delete private training files by stored name (idempotent)."""
    storage = Datawajahnew._meta.get_field('image_user').storage
    for name in names:
        if name:
            storage.delete(name)

def purge_face_training_data(user):
    """Delete a user's private training files and rebuild/remove the model."""
    purge_training_files(capture_training_file_names(user))
    return rebuild_face_model()

def render_recognition_image(image, results, authorized):
    """Annotate ``image`` with the recognition outcome and return JPEG bytes.

    All boxes carry the same (overall) verdict so a partially-matched frame
    cannot visually hint that one face was accepted while the request is denied.
    """
    _require_dependencies()
    color = (0, 255, 0) if authorized else (0, 0, 255)
    label = 'Authorized' if authorized else 'Unauthorized'
    for result in results:
        x, y, width, height = result['face_box']
        cv2.rectangle(image, (x, y), (x + width, y + height), color, 2)
        cv2.putText(
            image,
            label,
            (x, max(y - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )
    encoded, image_buffer = cv2.imencode('.jpg', image)
    if not encoded:
        raise InvalidFaceImage('Unable to encode the recognition image.')
    return image_buffer.tobytes()

def recognize_faces(image, label_to_user):
    """Match faces in ``image`` against the current model.

    ``label_to_user`` maps trained integer labels to owner face ids; any label
    missing from it (deleted/deactivated/downgraded owner) fails closed.
    """
    _require_dependencies()
    if not FACE_MODEL_PATH.is_file():
        raise FaceRecognitionUnavailable('No biometric model has been trained.')

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(str(FACE_MODEL_PATH))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = _cascade().detectMultiScale(
        gray,
        scaleFactor=DETECT_SCALE_FACTOR,
        minNeighbors=DETECT_MIN_NEIGHBORS,
        minSize=DETECT_MIN_SIZE,
    )
    results = []
    for x, y, width, height in faces:
        face_image = gray[y:y + height, x:x + width]
        label, confidence = recognizer.predict(face_image)
        authorized = confidence < 50 and label in label_to_user
        results.append({
            'face_id': label if authorized else None,
            'confidence': round(float(confidence), 2),
            'authorized': authorized,
            'face_box': (x, y, width, height),
        })
    return results
