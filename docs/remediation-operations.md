# Staged Production Remediation Runbook

## Overview and Safety Principles

This runbook defines the ordered, actionable deployment procedure to remediate backend security findings across secrets management, filesystem isolation, database migration prerequisites, media migration, and infrastructure boundaries.

### Safety Rules
- **Never expose plaintext secret values** in command arguments, logs, or commit messages.
- **Never run live migrations or media migrations without verified database and media backups**.
- **Distinguish unverified infrastructure**: Production database hosts, Redis clusters, MQTT brokers, and TLS proxies cannot be verified within this codebase repository alone; their network topology and TLS configurations must be validated on the deployment target.

---

## Stage 1: Secrets Rotation and Environment Hygiene

### 1.1 Rotate Core Secrets
Generate fresh cryptographically random values (minimum 32 ASCII characters / 256 bits of entropy) for all core production variables:
- `SECRET_KEY`: Rotated in Django settings. Note: Invalidation of active Django sessions is immediate upon change.
- `JWT_SIGNING_KEY`: Rotated to invalidate all previously issued access and refresh JWTs.
- `DB_PASSWORD`: Rotated in PostgreSQL first, then updated in the runtime environment.
- `SMARTLOCKER_DEVICE_TOKEN`: Minimum 32 ASCII characters. Must be synchronized with firmware before deployment.
- `MQTT_PASSWORD`: Rotated on the MQTT broker ACL configuration and runtime environment.

Example generation command:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 1.2 Git History Hygiene and Tracked Secret Removal
- **Commit the user's existing `.env.docker` deletion**: the working tree already shows `.env.docker` as deleted. Stage and commit that removal so the secret file is no longer tracked. **Do not restore or re-add `.env.docker`** (or any real `.env`) under any circumstances.
- Ensure `.env.docker` and any real `.env` files remain removed from the working tree and index.
- Check `.gitignore` and `.dockerignore` to confirm `.env*` files are excluded from future commits and Docker builds.
- Execute controlled Git history scrubbing for any previously committed `.env.docker` or secret commits on all branches. Commit the removal in the working clone first, then run `git filter-repo` in a clean clone, then force-push only after coordination:
  ```bash
  # In the working clone: stage/commit the removal first
  git rm --cached .env.docker && git commit -m "Remove tracked .env.docker"
  # In a clean clone: scrub history
  git filter-repo --path .env.docker --invert-paths --force
  ```
- **Legacy biometric model artifact (`apps/hasiltraining/lbph_model.xml`)**: on this checkout `git ls-files --error-unmatch apps/hasiltraining/lbph_model.xml` **succeeds**, so the path is still tracked in `HEAD`, while the working-tree file is **deleted**. `git log --all --full-history -- apps/hasiltraining/lbph_model.xml` returns commit `a4fb1c8`, so it is also present in history. Required steps:
  1. **Commit the existing working-tree deletion in the working clone** so the current path is untracked before any history rewrite:
     ```bash
     git rm apps/hasiltraining/lbph_model.xml
     git commit -m "Remove legacy public-path LBPH model artifact"
     ```
  2. **Run the controlled history scrub in a clean clone** to remove the artifact from prior commits on all branches:
     ```bash
     git filter-repo --path apps/hasiltraining/lbph_model.xml --invert-paths --force
     ```
  3. **Force-push only after coordination** with all repository contributors.
  4. **Verify afterward** that neither the current tree nor history retains the artifact (e.g. re-run `git ls-files apps/hasiltraining/lbph_model.xml` and `git log --all --full-history -- apps/hasiltraining/lbph_model.xml`).
  The quarantined copy stays out of the repository; the current runtime model is generated under `PRIVATE_MEDIA_ROOT` instead (see `docs/facerecognition_setup.md`).
- **Tracked private payment-proof files under `media/payment_proofs/`**: exactly **8 files** in that directory are currently Git-tracked (one text fixture plus JPEGs). A `.gitignore` entry of `media/` does **not** untrack files that are already tracked, so the ignore rule alone is insufficient. This is an **operator blocker**, not something this documentation change removed. Handle it in Stage 5 after backups and a verified `migrate_private_uploads` run:
  1. **Untrack** the directory from the current commit without touching the working-tree copies, then **commit the staged removal in the working clone** before any history rewrite:
     ```bash
     git rm -r --cached media/payment_proofs
     git commit -m "Untrack private payment proof uploads"
     ```
  2. **Run the controlled history scrub in a clean clone** to remove `media/payment_proofs/` from Git history and all branches:
     ```bash
     git filter-repo --path media/payment_proofs --invert-paths --force
     ```
  3. **Force-push only after coordination** with all repository contributors.
  4. **Verify afterward** that `git ls-files media/payment_proofs/` returns nothing and history no longer contains the path.
  Do **not** print these filenames or their contents in tickets/logs, and do **not** delete or move the source copies until the private copy and the Stage 2 backups are verified (see Stage 5.4).
- Force-push scrubbed branches only after coordinating with all repository contributors.

---

## Stage 2: Database and Media Backups

Before applying schema migrations or running data migration commands:

### 2.1 Database Snapshot
```bash
# PostgreSQL backup (substitute prod host/user):
pg_dump -h "${DB_HOST}" -U "${DB_USER}" -d "${DB_NAME}" -F c -b -v -f "/backups/smartlocker_pre_remediation_$(date +%Y%m%d%H%M%S).dump"
```

### 2.2 Media Storage Snapshot
Archive both public media and any existing private media folders:
```bash
tar -czvf "/backups/media_pre_remediation_$(date +%Y%m%d%H%M%S).tar.gz" -C /app media private_media
```

---

## Stage 3: Preflight Inspection for Migration `0011`

Migration `apps/marketplace/migrations/0011_transaction_state_and_constraints.py` enforces data integrity checks in `validate_legacy_rows` that will raise a `RuntimeError` and abort migration if corrupt legacy records exist.

Run the preflight query against the database prior to running `migrate`:
```sql
-- 1. Check for negative product prices
SELECT id, price FROM marketplace_product WHERE price < 0;

-- 2. Check for invalid transaction quantities (< 1)
SELECT id, quantity FROM marketplace_transaction WHERE quantity < 1;

-- 3. Check for negative transaction totals (< 0)
SELECT id, total_price FROM marketplace_transaction WHERE total_price < 0;

-- 4. Check for legacy AWAITING_PICKUP transactions (the filter is status-only)
SELECT id, status FROM marketplace_transaction WHERE status = 'AWAITING_PICKUP';
```

### Remediation of Failing Preflight Records:
- **Negative prices/totals/quantities**: Correct data via business reconciliation scripts or mark records cancelled/archived with explicit audited SQL before migrating.
- **`AWAITING_PICKUP` records**: `validate_legacy_rows` selects **every** row with `status='AWAITING_PICKUP'` and raises `RuntimeError` if any exist. It does **not** inspect `locker_id` or any other column, so the check is status-only: **all** `AWAITING_PICKUP` rows block migration — not just unbound ones — until each is physically or business reconciled and transitioned to a terminal status (e.g. `COMPLETED` or `REJECTED`).

---

## Stage 4: Apply Database Migrations

Once preflight conditions pass:
```bash
python manage.py migrate
```
This updates database tables, applies new constraints, links locker requests to transaction/delivery records, and configures storage routing for private uploads.

### 4.1 Note on `marketplace.0003_product_image.py` (already-applied databases)
The destructive database operations were **removed** from `apps/marketplace/migrations/0003_product_image.py`: the original `DROP COLUMN ... CASCADE` statements are gone and the node now uses `SeparateDatabaseAndState` with `database_operations=[]` (state-only field definitions, so the migration graph and model state are unchanged).

- **Databases that have not yet applied `0003`** now migrate through the node without dropping the `Product.image` column or pre-existing `created_at`/`updated_at` timestamps.
- **Databases that already applied the old `0003`** are unaffected going forward: Django records applied migrations and **will not re-run** `0003`, and the edited forward migration does **not** recover any data already dropped.
- Therefore, a deployment that previously traversed the old `DROP` path must inspect its backups for lost `Product.image` filenames (and any pre-existing timestamps) and restore **only** from a verified backup. Do not treat the forward migration as a data-recovery mechanism.

---

## Stage 5: Private Media Permissions and Upload Migration

### 5.1 Storage Directory Setup
Ensure `PRIVATE_MEDIA_ROOT` and `MEDIA_ROOT` are distinct and non-nested.
Directory permissions must be clamped to `0750` with ownership assigned to the application container user (`smartlocker`, UID `10001`, GID `10001`):
```bash
mkdir -p "${PRIVATE_MEDIA_ROOT}"
chown -R 10001:10001 "${PRIVATE_MEDIA_ROOT}"
chmod 0750 "${PRIVATE_MEDIA_ROOT}"
```

### 5.2 Execute `migrate_private_uploads`
The command moves sensitive files from `MEDIA_ROOT` to `PRIVATE_MEDIA_ROOT`, verifies byte equality via SHA-256, clamps destination file permissions to `0640` / `0750`, and removes the public source.

1. **Dry Run First**:
   ```bash
   python manage.py migrate_private_uploads --dry-run
   ```
   Inspect the reported summary: verify referenced row counts for:
   - `Transaction.payment_proof`
   - `Datawajahnew.image_user`
   - `Logsmartaccess2.image`
   and orphan counts across `payment_proofs/`, `imagetraining/`, `tracking/`, and `qris_images/`.

2. **Live Execution** (run ONLY after Step 2 backup verification):
   ```bash
   python manage.py migrate_private_uploads
   ```

### 5.3 Verify Zero Public Serving of Private Media
- Confirm web server config (Nginx/Caddy/Traefik) has **NO** location directive pointing to `PRIVATE_MEDIA_ROOT`.
- Confirm `SERVE_MEDIA=False` in production environment settings.
- Direct HTTP requests to `/media/face-training/`, `/media/payment_proofs/`, or `/private_media/` must return `404 Not Found` or `403 Forbidden` from the outer proxy.

### 5.4 Untrack Git-tracked private proof files (`media/payment_proofs/`)
**Precondition (all must hold before touching tracked content or source copies):**
- Stage 2 database and media backups are complete and verified.
- `migrate_private_uploads --dry-run` reported no errors, and the live run completed with the referenced/orphan counts accounted for.
- The private copies under `PRIVATE_MEDIA_ROOT` are verified (byte-equality via the command's SHA-256 check).

**Observed current state (audit finding — operator blocker, not resolved by this document):**
- Exactly **8 files** under `media/payment_proofs/` are currently Git-tracked (one text fixture plus JPEGs).
- `.gitignore` lists `media/`, but that does **not** untrack already-tracked files.

**Operator steps (only after the preconditions above):**
1. Untrack the directory from current tracked content **without deleting the working-tree copies**, then **commit the staged removal in the working clone** before any history rewrite:
   ```bash
   git rm -r --cached media/payment_proofs
   git commit -m "Untrack private payment proof uploads"
   ```
2. Run the controlled history scrub **in a clean clone** to remove `media/payment_proofs/` from Git history and all branches:
   ```bash
   git filter-repo --path media/payment_proofs --invert-paths --force
   ```
3. **Force-push only after coordination** with all contributors.
4. Verify afterward: `git ls-files media/payment_proofs/` returns nothing and history no longer contains the path.

**Handling constraints:**
- Do **not** print these filenames or their contents in tickets, logs, or commit messages.
- Do **not** delete or move the source copies before the private copy and the Stage 2 backups are verified; if a source file is not yet in private storage, treat it as unverified and stop.
- Force-push scrubbed branches only after coordinating with all contributors (see Stage 1.2).

---

## Stage 6: IoT Device Contract and Network Protocols

### 6.1 Device Token Authentication Contract
- Header: `X-Device-Token: <token>`
- Implementation: `apps.iot.authentication.require_device_token` compares `X-Device-Token` against `settings.SMARTLOCKER_DEVICE_TOKEN` using `secrets.compare_digest`.
- Token requirement: Minimum 32 ASCII characters.
- Failure outcome: `403 Forbidden` with body `{"detail": "device_auth_failed"}`.

### 6.2 MQTT Command Contract
Locker dispatch commands are published to the broker via `publish_locker_command`:
- **Topic Pattern**: Configured by `MQTT_TOPIC_COMMAND`, defaulting to `smartlocker/{locker_number}/command` (must contain `{locker_number}`, no wildcards).
- **QoS**: `1`, `retain=False`.
- **Payload Schema**:
  ```json
  {
    "command_id": "<uuid-string>",
    "locker_number": "<locker-number-string>",
    "purpose": "<INBOUND_DEPOSIT|INBOUND_RETRIEVE|STORAGE_DEPOSIT|STORAGE_RETRIEVE|MARKETPLACE_DEPOSIT|MARKETPLACE_RETRIEVE>",
    "action": "OPEN",
    "expires_at": "<ISO-8601-timestamp>"
  }
  ```

### 6.3 IoT Event Ingest Contract (`POST /api/v1/iot/events/`)
Devices report hardware status back via the REST ingest endpoint:
- **Authentication**: `X-Device-Token: <token>` header required.
- **Request Body**:
  ```json
  {
    "event_id": "<uuid-string>",
    "command_id": "<uuid-string>",
    "event_type": "<LOCKER_OPENED|ITEM_DETECTED|ITEM_REMOVED|COMMAND_FAILED>",
    "payload": {}
  }
  ```
- **Duplicate Handling**: Idempotent by `event_id`. A duplicate whose `command_id`, `event_type`, and `payload` all match returns `200 OK`; a reuse of the same `event_id` with different event data returns `409 Conflict` (`DeviceEventConflict`).
- **State Handling (exact outcomes from `process_device_event`)**:
  - **Late event → reconciliation**: If the command is still in an active status (`PENDING`, `SENT`, `OPENED`, `UNKNOWN`) but `expires_at` has already passed, the command is set to `UNKNOWN` with `error_code='late_device_event'` and the response is `409 Conflict` carrying `reconciliation_required: true`. This is the **only** path that sets `reconciliation_required`; it requires staff reconciliation (Stage 7.2).
  - **Compatible `COMMAND_FAILED`**: A `COMMAND_FAILED` event on a live, unexpired command is applied normally — the command transitions to `FAILED` (via `_handle_command_failure`) and the response is `200 OK`/`201 Created` (no `reconciliation_required`). It is **not** a 409.
  - **Incompatible event → plain 409**: Any event that does not match the command state — e.g. `ITEM_DETECTED` on a non-deposit purpose, `ITEM_REMOVED` on a non-retrieve purpose, an unknown event type, or an event against a command that is no longer in an active status — raises `InvalidDeviceEvent` and returns a plain `409 Conflict` **without** `reconciliation_required`.
  - Do not assume every device/sensor failure yields `reconciliation_required`; only the late-event-after-expiry case does.

---

## Stage 7: Expired Locker Command Workflow & Staff Reconciliation

Commands expire after 5 minutes (`COMMAND_TTL = timedelta(minutes=5)`).

### 7.1 Scheduled Expiry Job
Run the management command periodically via cron or Celery beat (every 1 to 5 minutes):
```bash
python manage.py expire_locker_commands
```
Behavior:
- Identifies commands in `PENDING`, `SENT`, `OPENED`, or `UNKNOWN` past `expires_at`.
- Updates command status to `UNKNOWN` with `error_code='expired_reconciliation'`.
- **Does not release the locker reservation**: The locker remains locked/reserved until physical inspection.

### 7.2 Staff Physical Reconciliation Workflow
Staff members inspect the physical locker slot and resolve the command via:
`POST /api/v1/lockers/commands/{command_id}/reconcile/`

- **Authorization**: Staff user credentials (`is_staff=True`).
- **Required Body**:
  ```json
  {
    "decision": "retry | confirm_failed | confirm_completed",
    "confirmation": "<confirmation_string>"
  }
  ```
- **Decisions** (all require the command to be in `UNKNOWN` status; `reconcile_locker_command` raises `ReconciliationDenied` → `409 Conflict` otherwise):
  1. `retry`: Allowed **only while the command is still unexpired** (`expires_at > now`). Re-dispatches the same MQTT command via `_dispatch_command`; it does not touch locker bookkeeping.
  2. `confirm_failed`: Allowed **only once the command is expired** (`expires_at <= now`) and requires `confirmation: "DEVICE_CONFIRMED_CLOSED"`. Sets the command to `FAILED` (`error_code='admin_reconciled_failed'`) and runs `_handle_command_failure`, which restores the **specific workflow's** transaction/delivery/locker bookkeeping rather than unconditionally freeing the locker:
     - `MARKETPLACE_DEPOSIT` → purchase locker cleared, locker `AVAILABLE`
     - `MARKETPLACE_RETRIEVE` → `otp_consumed_at` cleared, locker `OCCUPIED`
     - `INBOUND_DEPOSIT` → delivery back to `PENDING`, locker `AVAILABLE`
     - `INBOUND_RETRIEVE` → locker `OCCUPIED`
     - `STORAGE_DEPOSIT` → `storage_locker` cleared, locker `AVAILABLE`
     - `STORAGE_RETRIEVE` → locker `OCCUPIED`
  3. `confirm_completed`: Allowed **only once the command is expired** (`expires_at <= now`). Requires:
     - For deposit purposes: `confirmation: "DEVICE_CONFIRMED_ITEM_PRESENT"`. Sets locker status to `OCCUPIED` and command to `COMPLETED` (`error_code='admin_reconciled_completed'`).
     - For retrieval purposes: `confirmation: "DEVICE_CONFIRMED_ITEM_REMOVED"`. Sets locker status to `AVAILABLE` and command to `COMPLETED`.

> **Physical vs. logical**: This endpoint performs **server-side bookkeeping only**; it does **not** physically actuate the lock or open/close the locker. Staff must physically confirm the real locker state (closed / item present / item removed) and pass the matching confirmation string, after which the server restores the transaction, delivery, and locker records for that specific purpose.

---

## Stage 8: Manual Payment Verification Access Control

Payment proof uploads are strictly segregated:
- **Upload**: `POST /api/v1/marketplace/transactions/{id}/payment-proof/` (Buyer).
- **Proof Retrieval**: `GET /api/v1/marketplace/transactions/{id}/payment-proof/`.
- **Access Rule**: Restricted strictly to the **seller of the transaction** (`transaction.seller == request.user`) or an active **staff member** (`request.user.is_staff`). Buyers and unrelated users are denied (`403 Forbidden`).
- **Streaming**: Private file stream via `FileResponse` with non-PII filename `payment-proof`, MIME validation via Pillow header sniffing, and `Cache-Control: private, no-store`.

---

## Stage 9: Infrastructure and Proxy Hardening

### 9.1 `TRUST_X_FORWARDED_PROTO` Configuration
- Setting: `TRUST_X_FORWARDED_PROTO=False` by default in `.env.example`.
- **Safety Directive**: Set `TRUST_X_FORWARDED_PROTO=True` **ONLY** when:
  1. Django runs strictly behind a trusted reverse proxy (e.g. Nginx, AWS ALB, Caddy).
  2. The reverse proxy is explicitly configured to **strip or overwrite** incoming client `X-Forwarded-Proto` headers with `$scheme` (or `https`).
  3. Direct external network access to the Gunicorn/application port is blocked by firewall/security group.
- **Danger**: If set to `True` while direct network access exists, an attacker can spoof HTTPS via `X-Forwarded-Proto: https`, bypassing `SECURE_SSL_REDIRECT` and cookie protections.

### 9.2 Redis & Celery Security
- Configure `CELERY_BROKER_URL` and `CACHE_URL` with password authentication and TLS (`rediss://...`) in production.
- Disallow public Internet binding of Redis (`bind 127.0.0.1` or internal VPC subnet only).

### 9.3 MQTT Broker Hardening
- Require TLS (`MQTT_TLS=True` in production settings; mandatory when `DEBUG=False`).
- Require username/password authentication (`MQTT_USERNAME`, `MQTT_PASSWORD`).
- Configure Mosquitto/EMQX ACLs so locker client credentials can only publish to their designated topic prefix and cannot subscribe to system command channels.

**Backend (Django/Paho) TLS — `MQTT_CA_CERT` is a backend-only setting**:
- The Django backend reads `MQTT_TLS` and `MQTT_CA_CERT` in `apps/lockers/mqtt.py` (`client.tls_set(ca_certs=settings.MQTT_CA_CERT)`). When `MQTT_TLS=True` and `MQTT_CA_CERT` is configured, the backend verifies the broker certificate against that CA file.

**Locker firmware TLS — configured independently**:
- `MQTT_CA_CERT` and `MQTT_TLS` **do not configure the locker firmware**. Firmware must implement its own TLS policy and independently verify (or pin) the broker certificate; the backend setting has no effect on devices. Keep the firmware's trust policy in sync with the broker's certificate as a separate deployment concern.

### 9.4 Production Infrastructure Reality
> **NOTICE**: Host network interfaces, cloud security groups, reverse proxy configurations, Redis server authentication, and hardware firmware versions are **external infrastructure dependencies** not managed directly by this Django repository. Operational verification must be performed directly against the deployment environment.
