import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone

from apps.marketplace.models import Transaction
from apps.notifications.services import create_notifications

from .models import Delivery, Locker, LockerLog, LockerRequest
from .mqtt import MqttPublishError, publish_locker_command

COMMAND_TTL = timedelta(minutes=5)
OTP_TTL = timedelta(minutes=10)
MAX_OTP_ATTEMPTS = 5
ACTIVE_COMMAND_STATUSES = (
    LockerRequest.Status.PENDING,
    LockerRequest.Status.SENT,
    LockerRequest.Status.OPENED,
    LockerRequest.Status.UNKNOWN,
)


class LockerUnavailable(Exception):
    pass


class InvalidLockerWorkflow(Exception):
    pass


class InvalidPickupCode(Exception):
    pass


class PickupCodeExpired(Exception):
    pass


class PickupCodeLocked(Exception):
    pass


class DeviceCommandNotFound(Exception):
    pass


class InvalidDeviceEvent(Exception):
    pass


class DeviceEventConflict(Exception):
    pass


class ReconciliationDenied(Exception):
    pass

def _issue_pickup_code(purchase, *, now):
    otp = f'{secrets.randbelow(1_000_000):06d}'
    purchase.otp_hash = make_password(otp)
    purchase.otp_expires_at = now + OTP_TTL
    purchase.otp_consumed_at = None
    purchase.otp_failed_attempts = 0
    purchase.save(
        update_fields=[
            'otp_hash', 'otp_expires_at', 'otp_consumed_at',
            'otp_failed_attempts', 'updated_at',
        ]
    )
    create_notifications(
        user_ids=[purchase.buyer_id],
        title='Locker pickup code',
        body=f"Your item '{purchase.product.name}' is ready. Pickup code: {otp}. It expires in 10 minutes.",
    )



def _new_command(*, locker, actor, purpose, transaction=None, delivery=None):
    command = LockerRequest(
        locker=locker,
        requested_by=actor,
        purpose=purpose,
        transaction=transaction,
        delivery=delivery,
        expires_at=timezone.now() + COMMAND_TTL,
    )
    command.full_clean()
    command.save()
    LockerLog.objects.create(
        locker=locker,
        user=actor,
        command=command,
        transaction=transaction,
        delivery=delivery,
        action=LockerLog.Action.OPEN,
        is_successful=False,
        details='Locker open command queued.',
    )
    return command


def _dispatch_command(command_id):
    command = LockerRequest.objects.select_related('locker').get(pk=command_id)
    if command.status not in {LockerRequest.Status.PENDING, LockerRequest.Status.UNKNOWN}:
        return command
    if command.expires_at is None or command.expires_at <= timezone.now():
        LockerRequest.objects.filter(
            pk=command.pk,
            status__in=(LockerRequest.Status.PENDING, LockerRequest.Status.UNKNOWN),
        ).update(status=LockerRequest.Status.UNKNOWN, error_code='expired_reconciliation')
        return LockerRequest.objects.select_related('locker').get(pk=command.pk)

    try:
        publish_locker_command(command)
    except MqttPublishError:
        LockerRequest.objects.filter(
            pk=command.pk,
            status__in=(LockerRequest.Status.PENDING, LockerRequest.Status.UNKNOWN),
        ).update(status=LockerRequest.Status.UNKNOWN, error_code='mqtt_unavailable')
    else:
        LockerRequest.objects.filter(
            pk=command.pk,
            status__in=(LockerRequest.Status.PENDING, LockerRequest.Status.UNKNOWN),
        ).update(
            status=LockerRequest.Status.SENT,
            published_at=timezone.now(),
            error_code='',
        )
    return LockerRequest.objects.select_related('locker').get(pk=command.pk)


def _reserve_locker(locker):
    locker.status = Locker.LockerStatus.RESERVED
    locker.save(update_fields=['status'])


def _require_actor(actor, *, expected_user_id=None, role=None):
    if actor.is_staff:
        return
    if expected_user_id is not None and actor.pk != expected_user_id:
        raise PermissionError('This user is not authorized for the requested locker operation.')
    if role is not None and actor.role != role:
        raise PermissionError('This role is not authorized for the requested locker operation.')


def request_marketplace_deposit(*, transaction_id, actor):
    with db_transaction.atomic():
        purchase = Transaction.objects.select_for_update(of=('self',)).get(pk=transaction_id)
        _require_actor(actor, expected_user_id=purchase.seller_id)
        if purchase.status != Transaction.TransactionStatus.PAYMENT_VERIFIED or purchase.locker_id:
            raise InvalidLockerWorkflow
        if LockerRequest.objects.filter(
            transaction_id=purchase.pk,
            status__in=ACTIVE_COMMAND_STATUSES,
        ).exists():
            raise InvalidLockerWorkflow

        locker = (
            Locker.objects.select_for_update(of=('self',))
            .filter(type=Locker.LockerType.MARKETPLACE, status=Locker.LockerStatus.AVAILABLE)
            .order_by('pk')
            .first()
        )
        if locker is None:
            raise LockerUnavailable

        purchase.locker = locker
        purchase.save(update_fields=['locker', 'updated_at'])
        _reserve_locker(locker)
        command = _new_command(
            locker=locker,
            actor=actor,
            purpose=LockerRequest.Purpose.MARKETPLACE_DEPOSIT,
            transaction=purchase,
        )
    return _dispatch_command(command.pk)


def request_marketplace_retrieval(*, transaction_id, buyer, otp):
    failure = None
    command = None
    with db_transaction.atomic():
        purchase = Transaction.objects.select_for_update(of=('self',)).get(pk=transaction_id)
        _require_actor(buyer, expected_user_id=purchase.buyer_id)
        now = timezone.now()
        if purchase.status != Transaction.TransactionStatus.AWAITING_PICKUP:
            raise InvalidLockerWorkflow
        if not purchase.locker_id:
            raise InvalidLockerWorkflow
        if purchase.otp_failed_attempts >= MAX_OTP_ATTEMPTS:
            failure = PickupCodeLocked
        elif purchase.otp_consumed_at is not None or not purchase.otp_hash:
            failure = InvalidPickupCode
        elif purchase.otp_expires_at is None or purchase.otp_expires_at <= now:
            failure = PickupCodeExpired
        elif not check_password(otp, purchase.otp_hash):
            purchase.otp_failed_attempts += 1
            purchase.save(update_fields=['otp_failed_attempts', 'updated_at'])
            failure = PickupCodeLocked if purchase.otp_failed_attempts >= MAX_OTP_ATTEMPTS else InvalidPickupCode
        else:
            locker = Locker.objects.select_for_update(of=('self',)).get(pk=purchase.locker_id)
            if (
                locker.type != Locker.LockerType.MARKETPLACE
                or locker.status != Locker.LockerStatus.OCCUPIED
            ):
                raise InvalidLockerWorkflow
            _reserve_locker(locker)
            purchase.otp_consumed_at = now
            purchase.save(update_fields=['otp_consumed_at', 'updated_at'])
            command = _new_command(
                locker=locker,
                actor=buyer,
                purpose=LockerRequest.Purpose.MARKETPLACE_RETRIEVE,
                transaction=purchase,
            )

    if failure is PickupCodeLocked:
        raise PickupCodeLocked
    if failure is PickupCodeExpired:
        raise PickupCodeExpired
    if failure is InvalidPickupCode:
        raise InvalidPickupCode
    return _dispatch_command(command.pk)


def reissue_marketplace_pickup_code(*, transaction_id, actor):
    with db_transaction.atomic():
        purchase = Transaction.objects.select_for_update(of=('self',)).get(pk=transaction_id)
        _require_actor(actor, expected_user_id=purchase.seller_id)
        now = timezone.now()
        if purchase.status != Transaction.TransactionStatus.AWAITING_PICKUP or not purchase.locker_id:
            raise InvalidLockerWorkflow
        locker = Locker.objects.select_for_update(of=('self',)).get(pk=purchase.locker_id)
        if locker.type != Locker.LockerType.MARKETPLACE or locker.status != Locker.LockerStatus.OCCUPIED:
            raise InvalidLockerWorkflow
        if purchase.otp_expires_at and purchase.otp_expires_at > now and purchase.otp_failed_attempts < MAX_OTP_ATTEMPTS:
            raise InvalidLockerWorkflow
        if LockerRequest.objects.filter(
            transaction_id=purchase.pk,
            purpose=LockerRequest.Purpose.MARKETPLACE_RETRIEVE,
            status__in=ACTIVE_COMMAND_STATUSES,
        ).exists():
            raise InvalidLockerWorkflow
        _issue_pickup_code(purchase, now=now)
    return purchase


def request_delivery_inbound(*, delivery_id, courier):
    with db_transaction.atomic():
        delivery = Delivery.objects.select_for_update(of=('self',)).get(pk=delivery_id)
        _require_actor(courier, expected_user_id=delivery.courier_id)
        if delivery.status != Delivery.DeliveryStatus.PENDING or not delivery.owner_id:
            raise InvalidLockerWorkflow
        locker = Locker.objects.select_for_update(of=('self',)).get(pk=delivery.locker_id)
        if locker.type != Locker.LockerType.INBOUND or locker.status != Locker.LockerStatus.AVAILABLE:
            raise LockerUnavailable
        delivery.status = Delivery.DeliveryStatus.VERIFIED
        delivery.save(update_fields=['status', 'updated_at'])
        _reserve_locker(locker)
        command = _new_command(
            locker=locker,
            actor=courier,
            purpose=LockerRequest.Purpose.INBOUND_DEPOSIT,
            delivery=delivery,
        )
    return _dispatch_command(command.pk)


def request_delivery_transfer(*, delivery_id, courier):
    with db_transaction.atomic():
        delivery = Delivery.objects.select_for_update(of=('self',)).get(pk=delivery_id)
        _require_actor(courier, expected_user_id=delivery.courier_id)
        if delivery.status != Delivery.DeliveryStatus.DEPOSITED or delivery.storage_locker_id:
            raise InvalidLockerWorkflow
        locker = Locker.objects.select_for_update(of=('self',)).get(pk=delivery.locker_id)
        if locker.status != Locker.LockerStatus.OCCUPIED:
            raise LockerUnavailable
        _reserve_locker(locker)
        command = _new_command(
            locker=locker,
            actor=courier,
            purpose=LockerRequest.Purpose.INBOUND_RETRIEVE,
            delivery=delivery,
        )
    return _dispatch_command(command.pk)


def request_delivery_storage(*, delivery_id, courier):
    with db_transaction.atomic():
        delivery = Delivery.objects.select_for_update(of=('self',)).get(pk=delivery_id)
        _require_actor(courier, expected_user_id=delivery.courier_id)
        if delivery.status != Delivery.DeliveryStatus.DEPOSITED or delivery.storage_locker_id:
            raise InvalidLockerWorkflow
        if not delivery.owner_id:
            raise InvalidLockerWorkflow

        storage_locker_ids = list(
            Locker.objects.filter(
                type=Locker.LockerType.STORAGE,
                status=Locker.LockerStatus.AVAILABLE,
            ).order_by('pk').values_list('pk', flat=True)
        )
        if not storage_locker_ids:
            raise LockerUnavailable
        locker_ids = sorted({delivery.locker_id, *storage_locker_ids})
        locked_lockers = {
            locker.pk: locker
            for locker in Locker.objects.select_for_update(of=('self',))
            .filter(pk__in=locker_ids)
            .order_by('pk')
        }
        inbound_locker = locked_lockers.get(delivery.locker_id)
        if inbound_locker is None or inbound_locker.status != Locker.LockerStatus.AVAILABLE:
            raise InvalidLockerWorkflow
        storage_locker = next(
            (
                locked_lockers[locker_id]
                for locker_id in storage_locker_ids
                if locker_id in locked_lockers
                and locked_lockers[locker_id].status == Locker.LockerStatus.AVAILABLE
            ),
            None,
        )
        if storage_locker is None:
            raise LockerUnavailable
        delivery.storage_locker = storage_locker
        delivery.save(update_fields=['storage_locker', 'updated_at'])
        _reserve_locker(storage_locker)
        command = _new_command(
            locker=storage_locker,
            actor=courier,
            purpose=LockerRequest.Purpose.STORAGE_DEPOSIT,
            delivery=delivery,
        )
    return _dispatch_command(command.pk)

def request_delivery_retrieval(*, delivery_id, owner):
    with db_transaction.atomic():
        delivery = Delivery.objects.select_for_update(of=('self',)).get(pk=delivery_id)
        _require_actor(owner, expected_user_id=delivery.owner_id)
        if delivery.status != Delivery.DeliveryStatus.COMPLETED or delivery.retrieved_at is not None:
            raise InvalidLockerWorkflow
        if delivery.storage_locker_id is None:
            raise InvalidLockerWorkflow
        locker = Locker.objects.select_for_update(of=('self',)).get(pk=delivery.storage_locker_id)
        if locker.status != Locker.LockerStatus.OCCUPIED:
            raise LockerUnavailable
        _reserve_locker(locker)
        command = _new_command(
            locker=locker,
            actor=owner,
            purpose=LockerRequest.Purpose.STORAGE_RETRIEVE,
            delivery=delivery,
        )
    return _dispatch_command(command.pk)


def _command_snapshot(command_id):
    snapshot = LockerRequest.objects.filter(command_id=command_id).values(
        'pk', 'locker_id', 'transaction_id', 'delivery_id', 'purpose', 'requested_by_id',
    ).first()
    if snapshot is None:
        raise DeviceCommandNotFound
    return snapshot


def _check_duplicate_event(*, event_id, command_id, event_type, payload):
    from apps.iot.models import IoTEvent

    existing = IoTEvent.objects.select_related('command').filter(event_id=event_id).first()
    if existing is None:
        return None
    # IoTEvent.command_id is the FK attname (LockerRequest.pk); the caller passes
    # the public command UUID, so compare against the related command's UUID.
    existing_command_id = existing.command.command_id if existing.command_id is not None else None
    if (
        existing_command_id != command_id
        or existing.event_type != event_type
        or existing.payload != payload
    ):
        raise DeviceEventConflict
    return existing


def _save_event_log(*, command, locker, action, successful, details):
    LockerLog.objects.create(
        locker=locker,
        user=command.requested_by,
        command=command,
        transaction_id=command.transaction_id,
        delivery_id=command.delivery_id,
        action=action,
        is_successful=successful,
        details=details,
    )


def _complete_deposit(*, command, purchase, delivery, locker, now):
    if command.transaction_id:
        if (
            command.purpose != LockerRequest.Purpose.MARKETPLACE_DEPOSIT
            or purchase.status != Transaction.TransactionStatus.PAYMENT_VERIFIED
            or purchase.locker_id != locker.pk
        ):
            raise InvalidDeviceEvent
        purchase.status = Transaction.TransactionStatus.AWAITING_PICKUP
        purchase.deposited_at = now
        purchase.save(update_fields=['status', 'deposited_at', 'updated_at'])
        _issue_pickup_code(purchase, now=now)
        return LockerLog.Action.DEPOSIT, 'Marketplace item deposited.'

    if delivery is None:
        raise InvalidDeviceEvent
    if command.purpose == LockerRequest.Purpose.INBOUND_DEPOSIT:
        if delivery.status != Delivery.DeliveryStatus.VERIFIED or delivery.locker_id != locker.pk:
            raise InvalidDeviceEvent
        delivery.status = Delivery.DeliveryStatus.DEPOSITED
        delivery.save(update_fields=['status', 'updated_at'])
        return LockerLog.Action.DEPOSIT, 'Courier deposited parcel in inbound locker.'
    if command.purpose == LockerRequest.Purpose.STORAGE_DEPOSIT:
        if (
            delivery.status != Delivery.DeliveryStatus.DEPOSITED
            or delivery.storage_locker_id != locker.pk
        ):
            raise InvalidDeviceEvent
        delivery.status = Delivery.DeliveryStatus.COMPLETED
        delivery.save(update_fields=['status', 'updated_at'])
        create_notifications(
            user_ids=[delivery.owner_id],
            title='Delivery stored',
            body=f'Your delivery {delivery.receipt_number} is ready for collection.',
        )
        return LockerLog.Action.DEPOSIT, 'Delivery parcel stored for recipient.'
    raise InvalidDeviceEvent


def _complete_removal(*, command, purchase, delivery, locker, now):
    if command.transaction_id:
        if (
            command.purpose != LockerRequest.Purpose.MARKETPLACE_RETRIEVE
            or purchase.status != Transaction.TransactionStatus.AWAITING_PICKUP
            or purchase.locker_id != locker.pk
            or purchase.otp_consumed_at is None
        ):
            raise InvalidDeviceEvent
        purchase.status = Transaction.TransactionStatus.COMPLETED
        purchase.otp_hash = ''
        purchase.otp_expires_at = None
        purchase.save(update_fields=['status', 'otp_hash', 'otp_expires_at', 'updated_at'])
        create_notifications(
            user_ids=[purchase.seller_id],
            title='Marketplace pickup completed',
            body=f"The buyer collected '{purchase.product.name}'.",
        )
        return LockerLog.Action.RETRIEVE, 'Marketplace item retrieved.'

    if delivery is None:
        raise InvalidDeviceEvent
    if command.purpose == LockerRequest.Purpose.INBOUND_RETRIEVE:
        if delivery.status != Delivery.DeliveryStatus.DEPOSITED or delivery.locker_id != locker.pk:
            raise InvalidDeviceEvent
        return LockerLog.Action.RETRIEVE, 'Courier removed parcel from inbound locker.'
    if command.purpose == LockerRequest.Purpose.STORAGE_RETRIEVE:
        if (
            delivery.status != Delivery.DeliveryStatus.COMPLETED
            or delivery.storage_locker_id != locker.pk
            or delivery.retrieved_at is not None
        ):
            raise InvalidDeviceEvent
        delivery.retrieved_at = now
        delivery.save(update_fields=['retrieved_at', 'updated_at'])
        return LockerLog.Action.RETRIEVE, 'Recipient retrieved stored delivery.'
    raise InvalidDeviceEvent


def _handle_command_failure(*, command, purchase, delivery, locker, now):
    if command.transaction_id:
        if command.purpose == LockerRequest.Purpose.MARKETPLACE_DEPOSIT:
            if purchase.status != Transaction.TransactionStatus.PAYMENT_VERIFIED:
                raise InvalidDeviceEvent
            purchase.locker = None
            purchase.save(update_fields=['locker', 'updated_at'])
            locker.status = Locker.LockerStatus.AVAILABLE
        elif command.purpose == LockerRequest.Purpose.MARKETPLACE_RETRIEVE:
            if purchase.status != Transaction.TransactionStatus.AWAITING_PICKUP:
                raise InvalidDeviceEvent
            purchase.otp_consumed_at = None
            purchase.save(update_fields=['otp_consumed_at', 'updated_at'])
            locker.status = Locker.LockerStatus.OCCUPIED
        else:
            raise InvalidDeviceEvent
    elif command.purpose == LockerRequest.Purpose.INBOUND_DEPOSIT:
        if delivery.status != Delivery.DeliveryStatus.VERIFIED:
            raise InvalidDeviceEvent
        delivery.status = Delivery.DeliveryStatus.PENDING
        delivery.save(update_fields=['status', 'updated_at'])
        locker.status = Locker.LockerStatus.AVAILABLE
    elif command.purpose == LockerRequest.Purpose.INBOUND_RETRIEVE:
        if delivery.status != Delivery.DeliveryStatus.DEPOSITED:
            raise InvalidDeviceEvent
        locker.status = Locker.LockerStatus.OCCUPIED
    elif command.purpose == LockerRequest.Purpose.STORAGE_DEPOSIT:
        if delivery.status != Delivery.DeliveryStatus.DEPOSITED:
            raise InvalidDeviceEvent
        delivery.storage_locker = None
        delivery.save(update_fields=['storage_locker', 'updated_at'])
        locker.status = Locker.LockerStatus.AVAILABLE
    elif command.purpose == LockerRequest.Purpose.STORAGE_RETRIEVE:
        if delivery.status != Delivery.DeliveryStatus.COMPLETED or delivery.retrieved_at is not None:
            raise InvalidDeviceEvent
        locker.status = Locker.LockerStatus.OCCUPIED
    else:
        raise InvalidDeviceEvent

    locker.save(update_fields=['status'])
    command.status = LockerRequest.Status.FAILED
    command.completed_at = now
    command.error_code = 'device_failed'
    command.save(update_fields=['status', 'completed_at', 'error_code'])
    return LockerLog.Action.OPEN, 'Device reported that the locker command failed.'


def process_device_event(*, event_id, command_id, event_type, payload):
    from apps.iot.models import IoTEvent

    duplicate = _check_duplicate_event(
        event_id=event_id,
        command_id=command_id,
        event_type=event_type,
        payload=payload,
    )
    if duplicate is not None:
        return duplicate, True, False

    snapshot = _command_snapshot(command_id)
    try:
        with db_transaction.atomic():
            duplicate = IoTEvent.objects.select_related('command').filter(event_id=event_id).first()
            if duplicate is not None:
                duplicate_command_id = (
                    duplicate.command.command_id if duplicate.command_id is not None else None
                )
                if (
                    duplicate_command_id != command_id
                    or duplicate.event_type != event_type
                    or duplicate.payload != payload
                ):
                    raise DeviceEventConflict
                return duplicate, True, False

            purchase = None
            delivery = None
            if snapshot['transaction_id'] is not None:
                purchase = Transaction.objects.select_for_update(of=('self',)).get(
                    pk=snapshot['transaction_id']
                )
            if snapshot['delivery_id'] is not None:
                delivery = Delivery.objects.select_for_update(of=('self',)).get(
                    pk=snapshot['delivery_id']
                )
            locker = Locker.objects.select_for_update(of=('self',)).get(pk=snapshot['locker_id'])
            command = LockerRequest.objects.select_for_update(of=('self',)).get(pk=snapshot['pk'])
            if (
                command.command_id != command_id
                or command.locker_id != locker.pk
                or command.transaction_id != snapshot['transaction_id']
                or command.delivery_id != snapshot['delivery_id']
            ):
                raise InvalidDeviceEvent

            event = IoTEvent.objects.create(
                event_id=event_id,
                command=command,
                user_id=command.requested_by_id,
                event_type=event_type,
                payload=payload,
            )
            now = timezone.now()
            if command.status not in ACTIVE_COMMAND_STATUSES:
                raise InvalidDeviceEvent
            if command.expires_at is None or command.expires_at <= now:
                command.status = LockerRequest.Status.UNKNOWN
                command.error_code = 'late_device_event'
                command.save(update_fields=['status', 'error_code'])
                _save_event_log(
                    command=command,
                    locker=locker,
                    action=LockerLog.Action.OPEN,
                    successful=False,
                    details='Late device event requires manual reconciliation.',
                )
                return event, False, True

            if event_type == IoTEvent.EventType.LOCKER_OPENED:
                command.status = LockerRequest.Status.OPENED
                locker.last_opened_by_id = command.requested_by_id
                locker.save(update_fields=['last_opened_by'])
                action, details, successful = LockerLog.Action.OPEN, 'Device confirmed locker opened.', True
            elif event_type == IoTEvent.EventType.ITEM_DETECTED:
                if command.purpose not in {
                    LockerRequest.Purpose.INBOUND_DEPOSIT,
                    LockerRequest.Purpose.STORAGE_DEPOSIT,
                    LockerRequest.Purpose.MARKETPLACE_DEPOSIT,
                }:
                    raise InvalidDeviceEvent
                action, details = _complete_deposit(
                    command=command,
                    purchase=purchase,
                    delivery=delivery,
                    locker=locker,
                    now=now,
                )
                command.status = LockerRequest.Status.COMPLETED
                command.completed_at = now
                locker.status = Locker.LockerStatus.OCCUPIED
                locker.save(update_fields=['status'])
                successful = True
            elif event_type == IoTEvent.EventType.ITEM_REMOVED:
                if command.purpose not in {
                    LockerRequest.Purpose.INBOUND_RETRIEVE,
                    LockerRequest.Purpose.STORAGE_RETRIEVE,
                    LockerRequest.Purpose.MARKETPLACE_RETRIEVE,
                }:
                    raise InvalidDeviceEvent
                action, details = _complete_removal(
                    command=command,
                    purchase=purchase,
                    delivery=delivery,
                    locker=locker,
                    now=now,
                )
                command.status = LockerRequest.Status.COMPLETED
                command.completed_at = now
                locker.status = Locker.LockerStatus.AVAILABLE
                locker.save(update_fields=['status'])
                successful = True
            elif event_type == IoTEvent.EventType.COMMAND_FAILED:
                action, details = _handle_command_failure(
                    command=command,
                    purchase=purchase,
                    delivery=delivery,
                    locker=locker,
                    now=now,
                )
                successful = False
            else:
                raise InvalidDeviceEvent

            if command.status != LockerRequest.Status.FAILED:
                command.error_code = ''
                command.save(update_fields=['status', 'completed_at', 'error_code'])
            _save_event_log(
                command=command,
                locker=locker,
                action=action,
                successful=successful,
                details=details,
            )
            return event, False, False
    except IntegrityError:
        duplicate = _check_duplicate_event(
            event_id=event_id,
            command_id=command_id,
            event_type=event_type,
            payload=payload,
        )
        if duplicate is not None:
            return duplicate, True, False
        raise

def _lock_command_context(snapshot):
    purchase = None
    delivery = None
    if snapshot.transaction_id:
        purchase = Transaction.objects.select_for_update(of=('self',)).get(pk=snapshot.transaction_id)
    if snapshot.delivery_id:
        delivery = Delivery.objects.select_for_update(of=('self',)).get(pk=snapshot.delivery_id)
    locker = Locker.objects.select_for_update(of=('self',)).get(pk=snapshot.locker_id)
    command = LockerRequest.objects.select_for_update(of=('self',)).get(pk=snapshot.pk)
    return purchase, delivery, locker, command


def reconcile_locker_command(*, command_id, actor, decision, confirmation=''):
    if not actor.is_staff:
        raise ReconciliationDenied
    snapshot = LockerRequest.objects.filter(command_id=command_id).first()
    if snapshot is None:
        raise DeviceCommandNotFound

    with db_transaction.atomic():
        purchase, delivery, locker, command = _lock_command_context(snapshot)
        if command.status != LockerRequest.Status.UNKNOWN:
            raise ReconciliationDenied
        now = timezone.now()
        if decision == 'retry':
            if command.expires_at is None or command.expires_at <= now:
                raise ReconciliationDenied
            LockerLog.objects.create(
                locker=locker,
                user=actor,
                command=command,
                transaction=purchase,
                delivery=delivery,
                action=LockerLog.Action.RECONCILE,
                details='Staff authorized retry of the same unexpired MQTT command.',
            )
        elif decision == 'confirm_failed':
            if command.expires_at is None or command.expires_at > now:
                raise ReconciliationDenied
            if confirmation != 'DEVICE_CONFIRMED_CLOSED':
                raise ReconciliationDenied
            _handle_command_failure(
                command=command,
                purchase=purchase,
                delivery=delivery,
                locker=locker,
                now=now,
            )
            command.error_code = 'admin_reconciled_failed'
            command.save(update_fields=['error_code'])
            LockerLog.objects.create(
                locker=locker,
                user=actor,
                command=command,
                transaction=purchase,
                delivery=delivery,
                action=LockerLog.Action.RECONCILE,
                is_successful=True,
                details='Staff confirmed device closed and reconciled expired command as failed.',
            )
        elif decision == 'confirm_completed':
            if command.expires_at is None or command.expires_at > now:
                raise ReconciliationDenied
            deposit_purposes = {
                LockerRequest.Purpose.INBOUND_DEPOSIT,
                LockerRequest.Purpose.STORAGE_DEPOSIT,
                LockerRequest.Purpose.MARKETPLACE_DEPOSIT,
            }
            if command.purpose in deposit_purposes:
                if confirmation != 'DEVICE_CONFIRMED_ITEM_PRESENT':
                    raise ReconciliationDenied
                _, details = _complete_deposit(
                    command=command,
                    purchase=purchase,
                    delivery=delivery,
                    locker=locker,
                    now=now,
                )
                locker.status = Locker.LockerStatus.OCCUPIED
            else:
                if confirmation != 'DEVICE_CONFIRMED_ITEM_REMOVED':
                    raise ReconciliationDenied
                _, details = _complete_removal(
                    command=command,
                    purchase=purchase,
                    delivery=delivery,
                    locker=locker,
                    now=now,
                )
                locker.status = Locker.LockerStatus.AVAILABLE
            locker.save(update_fields=['status'])
            command.status = LockerRequest.Status.COMPLETED
            command.completed_at = now
            command.error_code = 'admin_reconciled_completed'
            command.save(update_fields=['status', 'completed_at', 'error_code'])
            LockerLog.objects.create(
                locker=locker,
                user=actor,
                command=command,
                transaction=purchase,
                delivery=delivery,
                action=LockerLog.Action.RECONCILE,
                is_successful=True,
                details=f'Staff confirmed physical completion: {details}',
            )
        else:
            raise ValidationError({'decision': 'Use retry, confirm_failed, or confirm_completed.'})

    if decision == 'retry':
        return _dispatch_command(snapshot.pk)
    return LockerRequest.objects.select_related('locker').get(pk=snapshot.pk)
