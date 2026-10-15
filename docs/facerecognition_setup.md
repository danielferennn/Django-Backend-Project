# Face Recognition Setup and Biometrics Lifecycle

## Overview and Security Model

The biometric module provides face recognition for owner-role physical locker operations. It uses OpenCV Haar cascades for face detection and Local Binary Pattern Histograms (LBPH) for face recognition.

> **CRITICAL SECURITY NOTICE: NO LIVENESS / ANTI-SPOOFING**  
> The LBPH face recognition pipeline processes static 2D images. It contains **no liveness detection**, **no 3D depth verification**, and **no anti-spoofing countermeasures**. It can be spoofed using printed photos or digital screens displaying an owner face.  
> **Face recognition MUST NOT be the sole unlock factor** in any production workflow. It must operate strictly in combination with another verification factor (e.g., OTP pickup code, authenticated session, or physical credential).

> **SCOPE: RESULT RECORDING ONLY — NO LOCK ACTUATION**  
> This module only ingests a camera image from a device (`createlogusersmartnew/`), computes an `Authorized`/`Unauthorized` recognition result, and stores the result plus an annotated capture (`Logsmartaccess2`). It **does not itself publish MQTT locker commands, open a locker, or actuate hardware**; locker command dispatch lives in the `lockers` app (`publish_locker_command`) and is driven by the locker workflow/command endpoints. Treat the face result as one input signal, never as a direct unlock trigger.

---

## Storage and Permissions Architecture

Biometric assets (training images, camera access captures, and the trained LBPH model) are treated as sensitive biometric PII and are isolated from public media storage:

1. **Private Media Root (`PRIVATE_MEDIA_ROOT`)**:
   - Training images are stored in `face-training/<user_id>/<uuid><ext>` under `PRIVATE_MEDIA_ROOT`. `upload_image_training2` preserves a lowercase `jpg`/`jpeg`/`png`/`webp` extension from the original filename; any other extension (or none) is normalized to `.jpg`.
   - Access attempt logs are stored in `face-access-logs/<uuid>.jpg` under `PRIVATE_MEDIA_ROOT`.
   - The trained LBPH model artifact is written to `biometrics/lbph_model.xml` under `PRIVATE_MEDIA_ROOT`.
   - The temporary advisory lock file is written to `biometrics/lbph_model.xml.lock`.
2. **Filesystem Security Boundaries**:
   - All private media directories are clamped to mode `0750` (`rwxr-x---`).
   - Private media files and model files are clamped to mode `0640` (`rw-r-----`).
   - Lock files are clamped to mode `0600` (`rw-------`).
   - Container processes execute as non-root user `smartlocker` (UID `10001`, GID `10001`).
3. **No Direct Public Serving**:
   - `PRIVATE_MEDIA_ROOT` is **never** registered under Django static URL routing and must never be exposed via Nginx/Caddy location blocks.
   - Files are streamed strictly via authenticated Django endpoints returning `FileResponse` with headers `Cache-Control: private, no-store` and `X-Content-Type-Options: nosniff`.

---

## Authentication, Roles, and API Endpoints

All face recognition endpoints reside under `/api/v1/facerecognition/`:

| Endpoint | Method | Auth / Credentials | Roles / Permissions | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `createimagetrainingusernew/` | `POST` | Bearer JWT | `OWNER` (or `is_staff` on behalf of owner via `username` param) | Upload 1 to 10 training images; validates single face per image; rebuilds model |
| `getuserimageexists/` | `GET` | Bearer JWT | `OWNER` (or `is_staff` via `username` query param) | List registered training image metadata and private stream URLs |
| `training-images/<int:pk>/` | `GET` | Bearer JWT | Image owner or `is_staff` | Stream private training image file |
| `training-images/<int:pk>/` | `DELETE`| Bearer JWT | Image owner or `is_staff` | Delete training record and file; atomically rebuilds LBPH model |
| `createlogusersmartnew/` | `POST` | `X-Device-Token` header | IoT Device (constant-time token check) | Ingest camera capture from locker device; run LBPH match; log access |
| `getuserlogsmartnew/` | `GET` | Bearer JWT | `OWNER` (or `is_staff` via `username` query param) | View face access history logs for an owner |
| `access-logs/<str:log_id>/image/` | `GET` | Bearer JWT | Log owner or `is_staff` (`is_staff` required if log face is unknown/None) | Stream annotated camera capture file |

---

## Input Limits and Validation Rules

1. **Batch Upload Cap**:
   - `1` to `10` files per `POST /api/v1/facerecognition/createimagetrainingusernew/` request.
2. **Owner Lifetime Training Image Limit**:
   - **Maximum 10 total training images per owner**.
   - If `existing_count + incoming_uploads > 10`, the upload is rejected (`400 Bad Request`). Existing images must be deleted before enrolling new ones.
   - Owner row is locked with `select_for_update()` during validation to prevent concurrent batch bypass.
3. **File Size and Dimension Limits**:
   - Max file size: **5 MiB** (`5,242,880` bytes).
   - Max dimensions: **4096 x 4096** pixels.
   - Max canvas area: **12,000,000 pixels** (rejects decompression bombs before memory buffer allocation).
4. **Single-Face Validation**:
   - Every training image is checked with `haarcascade_frontalface_default.xml`.
   - The image must contain **exactly one detectable face**. Zero faces or multiple faces are rejected immediately (`400 Bad Request`).
5. **Rate Limiting**:
   - `POST createimagetrainingusernew/` is throttled by DRF `ScopedRateThrottle` (`scope: 'face_training'`).
   - Configured limit: **`20/hour`** per authenticated user.

---

## LBPH Model Rebuild Lifecycle

The current LBPH model is generated at runtime under `PRIVATE_MEDIA_ROOT` (`biometrics/lbph_model.xml`) and **must never be committed to Git**. It is dynamically generated and synchronized:

1. **Rebuild Triggers**:
   - **Upload**: Successfully enrolling new training images triggers `rebuild_face_model()`.
   - **Deletion**: Deleting a training image record (`DELETE training-images/<id>/`) triggers `rebuild_face_model()`.
   - **User Status / Role Change**: Django signal `post_save` on `User` watches `role` and `is_active`. If an owner is deactivated or role-downgraded, transaction on-commit handler runs `rebuild_face_model()`.
   - **User Deletion**: Admin user deletion calls `capture_training_file_names()`, removes DB records, purges files from private storage, and triggers `rebuild_face_model()`.
2. **Atomic Lock and Swap**:
   - Rebuilds acquire an exclusive advisory lock using `fcntl.flock` on `biometrics/lbph_model.xml.lock`.
   - Model is trained only on active users with `role=OWNER` and non-empty, digit `face_id`.
   - Output is saved to a unique temporary file (`.lbph_model.xml.<uuid>.tmp.xml`), chmod'd to `0640`, and atomically renamed to `lbph_model.xml`.
   - If zero eligible training samples exist, any existing `lbph_model.xml` is unlinked (`0` samples).
3. **Fail-Closed Verification**:
   - In `createlogusersmartnew/`, matches are accepted only if confidence $< 50$ AND the predicted label maps to a currently active `OWNER` in `_owner_label_map()`.
   - If the user was deactivated or downgraded, recognition fails closed (`Unauthorized`).
