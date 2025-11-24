from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
import logging

from apps.notifications.tasks import push_notification_task
from apps.lockers.models import Locker, LockerLog # New Import

from .models import IoTEvent


logger = logging.getLogger(__name__) # New Line

EVENT_MESSAGES = {
    'OTP_VALIDATED': 'Kode OTP berhasil diverifikasi oleh perangkat.',
    'TAMPER_DETECTED': 'Sensor mendeteksi kemungkinan gangguan pada locker.',
    'PARCEL_DETECTED': 'Paket baru terdeteksi di locker inbound.',
    'TRAPDOOR_OPENED': 'Trapdoor locker terbuka.',
    'TRAPDOOR_CLOSED': 'Trapdoor locker tertutup.',
    'RFID_ACCEPTED': 'RFID valid – akses diberikan.',
    'RFID_DENIED': 'RFID tidak valid – akses ditolak.',
    'LOCKER_OPENED': 'Locker dibuka melalui panel front-end.',
    'LOCKER_ACCESS_GRANTED': 'OTP valid – locker dibuka.',
    'LOCKER_ACCESS_DENIED': 'Percobaan OTP gagal.',
}


@receiver(post_save, sender=IoTEvent)
def notify_priority_events(sender, instance: IoTEvent, created: bool, **kwargs) -> None:
    if not created:
        return

    payload = instance.payload or {}
    event_key = (payload.get('event') or instance.event_type or '').upper()
    message = EVENT_MESSAGES.get(event_key)

    if not message:
        return

    target_ids = []
    if instance.user_id:
        target_ids.append(instance.user_id)
    if not target_ids:
        User = get_user_model()
        target_ids = list(User.objects.filter(is_superuser=True).values_list('id', flat=True))

    push_notification_task.delay( # Changed to .delay() for async execution
        user_ids=target_ids,
        title='SmartLocker Event',
        body=message,
    )


@receiver(post_save, sender=IoTEvent)
def handle_specific_locker_events(sender, instance: IoTEvent, created: bool, **kwargs) -> None:
    """
    Listens for new IoTEvent creations and triggers corresponding actions for
    LOCKER_OPENED and ITEM_DETECTED events.
    """
    if not created:
        return

    payload = instance.payload or {}
    event_type = instance.event_type
    locker_number = payload.get('locker_id') # Renamed from locker_id to locker_number for clarity

    if event_type not in [IoTEvent.EventType.LOCKER_OPENED, IoTEvent.EventType.ITEM_DETECTED]:
        return # Only process specific events here

    if not locker_number:
        logger.warning(f"IoTEvent {instance.id} of type {event_type} received without a locker_id in payload.")
        return

    try:
        locker = Locker.objects.get(number=locker_number)
    except Locker.DoesNotExist:
        logger.error(f"Received event for non-existent locker with number={locker_number}.")
        return

    # --- Handle Locker Opened Event ---
    if event_type == IoTEvent.EventType.LOCKER_OPENED:
        user_id = payload.get('user_id')
        user = None
        if user_id:
            try:
                User = get_user_model()
                user = User.objects.get(id=user_id)
                # Update the last person who opened the locker
                locker.last_opened_by = user
                locker.save(update_fields=['last_opened_by'])
            except User.DoesNotExist:
                logger.warning(f"User with id={user_id} not found for LOCKER_OPENED event.")
        
        # Create a log entry
        LockerLog.objects.create(
            locker=locker,
            user=user,
            action=LockerLog.Action.OPEN,
            details=f"Locker {locker.number} opened via IoT event."
        )

        # Determine who to notify
        notification_recipient = user or locker.last_opened_by
        if notification_recipient:
            push_notification_task.delay(
                user_ids=[notification_recipient.id],
                title="Loker Terbuka",
                body=f"Loker {locker.number} telah dibuka."
            )
        else:
            logger.info(f"No specific user to notify for LOCKER_OPENED event on locker {locker.number}. Notifying superusers if any.")
            # Fallback to superusers if no specific recipient, similar to notify_priority_events
            User = get_user_model()
            superuser_ids = list(User.objects.filter(is_superuser=True).values_list('id', flat=True))
            if superuser_ids:
                push_notification_task.delay(
                    user_ids=superuser_ids,
                    title="Loker Terbuka (Admin Notif)",
                    body=f"Loker {locker.number} telah dibuka, namun tidak ada pengguna spesifik yang teridentifikasi."
                )


    # --- Handle Item Detected Event ---
    elif event_type == IoTEvent.EventType.ITEM_DETECTED:
        # Update locker status
        locker.status = Locker.LockerStatus.OCCUPIED
        locker.save(update_fields=['status'])

        # Create a log entry
        LockerLog.objects.create(
            locker=locker,
            user=locker.last_opened_by, # User who last opened is likely the owner/depositor
            action=LockerLog.Action.DEPOSIT,
            details=f"Item detected inside locker {locker.number} via IoT event."
        )

        # Notify the last user who opened the locker
        if locker.last_opened_by:
            push_notification_task.delay(
                user_ids=[locker.last_opened_by.id],
                title="Barang Terdeteksi",
                body=f"Sebuah barang telah terdeteksi di dalam loker {locker.number}."
            )
        else:
            logger.info(f"No specific user to notify for ITEM_DETECTED event on locker {locker.number}. Notifying superusers if any.")
            # Fallback to superusers if no specific recipient
            User = get_user_model()
            superuser_ids = list(User.objects.filter(is_superuser=True).values_list('id', flat=True))
            if superuser_ids:
                push_notification_task.delay(
                    user_ids=superuser_ids,
                    title="Barang Terdeteksi (Admin Notif)",
                    body=f"Sebuah barang telah terdeteksi di dalam loker {locker.number}, namun tidak ada pengguna spesifik yang teridentifikasi."
                )

