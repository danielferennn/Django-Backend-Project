from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.tasks import push_notification_task

from .models import IoTEvent


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

    push_notification_task(
        user_ids=target_ids,
        title='SmartLocker Event',
        body=message,
    )
