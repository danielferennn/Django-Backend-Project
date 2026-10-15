from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.lockers.models import LockerRequest


class Command(BaseCommand):
    help = 'Mark expired locker commands unknown without releasing their reserved lockers.'

    def handle(self, *args, **options):
        now = timezone.now()
        updated = LockerRequest.objects.filter(
            status__in=(
                LockerRequest.Status.PENDING,
                LockerRequest.Status.SENT,
                LockerRequest.Status.OPENED,
                LockerRequest.Status.UNKNOWN,
            ),
            expires_at__lte=now,
        ).exclude(error_code='expired_reconciliation').update(
            status=LockerRequest.Status.UNKNOWN,
            error_code='expired_reconciliation',
        )
        self.stdout.write(f'Marked {updated} expired command(s) for manual reconciliation.')
