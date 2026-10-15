import re
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.iot.models import IoTEvent
from apps.marketplace.models import Product, Store, Transaction
from apps.notifications.models import Notification

from .models import Delivery, Locker, LockerLog, LockerRequest
from .services import (
    DeviceEventConflict,
    InvalidPickupCode,
    PickupCodeLocked,
    process_device_event,
    reconcile_locker_command,
    request_delivery_inbound,
    request_delivery_retrieval,
    request_delivery_storage,
    request_delivery_transfer,
    request_marketplace_deposit,
    request_marketplace_retrieval,
)


class LockerCommandLifecycleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user(
            username='seller',
            email='seller@example.com',
            password='test-password',
            role=User.Role.SELLER,
        )
        self.buyer = User.objects.create_user(
            username='buyer',
            email='buyer@example.com',
            password='test-password',
            role=User.Role.BUYER,
        )
        self.other_buyer = User.objects.create_user(
            username='other-buyer',
            email='other-buyer@example.com',
            password='test-password',
            role=User.Role.BUYER,
        )
        self.courier = User.objects.create_user(
            username='courier',
            email='courier@example.com',
            password='test-password',
            role=User.Role.COURIER,
        )
        self.market_locker = Locker.objects.create(
            number='M1', type=Locker.LockerType.MARKETPLACE,
        )
        self.inbound_locker = Locker.objects.create(
            number='I1', type=Locker.LockerType.INBOUND,
        )
        self.storage_locker = Locker.objects.create(
            number='S1', type=Locker.LockerType.STORAGE,
        )
        store = Store.objects.create(owner=self.seller, name='Test store')
        self.product = Product.objects.create(
            store=store,
            seller=self.seller,
            name='Item',
            price=Decimal('12.50'),
            stock=4,
            description='Test item',
        )
        self.purchase = Transaction.objects.create(
            buyer=self.buyer,
            seller=self.seller,
            product=self.product,
            quantity=1,
            total_price=Decimal('12.50'),
            status=Transaction.TransactionStatus.PAYMENT_VERIFIED,
        )
        self.delivery = Delivery.objects.create(
            receipt_number='TRACK-1',
            courier=self.courier,
            locker=self.inbound_locker,
            owner=self.buyer,
        )

    def send_event(self, command, event_type, *, event_id=None, payload=None):
        return process_device_event(
            event_id=event_id or uuid.uuid4(),
            command_id=command.command_id,
            event_type=event_type,
            payload=payload or {},
        )

    @patch('apps.lockers.services.publish_locker_command')
    def test_marketplace_deposit_and_one_time_pickup_code(self, publish):
        command = request_marketplace_deposit(transaction_id=self.purchase.pk, actor=self.seller)
        self.assertEqual(command.status, LockerRequest.Status.SENT)
        self.market_locker.refresh_from_db()
        self.assertEqual(self.market_locker.status, Locker.LockerStatus.RESERVED)
        publish.assert_called_once()

        event_id = uuid.uuid4()
        opened, duplicate, late = self.send_event(
            command,
            IoTEvent.EventType.LOCKER_OPENED,
            event_id=event_id,
        )
        self.assertFalse(duplicate)
        self.assertFalse(late)
        replay, duplicate, late = self.send_event(
            command,
            IoTEvent.EventType.LOCKER_OPENED,
            event_id=event_id,
        )
        self.assertTrue(duplicate)
        self.assertEqual(replay.pk, opened.pk)
        with self.assertRaises(DeviceEventConflict):
            self.send_event(
                command,
                IoTEvent.EventType.LOCKER_OPENED,
                event_id=event_id,
                payload={'different': True},
            )

        self.send_event(command, IoTEvent.EventType.ITEM_DETECTED)
        self.purchase.refresh_from_db()
        self.market_locker.refresh_from_db()
        self.assertEqual(self.purchase.status, Transaction.TransactionStatus.AWAITING_PICKUP)
        self.assertEqual(self.market_locker.status, Locker.LockerStatus.OCCUPIED)
        notification = Notification.objects.get(user=self.buyer, title='Locker pickup code')
        otp = re.search(r'Pickup code: (\d{6})', notification.body).group(1)
        self.assertNotEqual(self.purchase.otp_hash, otp)
        self.assertTrue(check_password(otp, self.purchase.otp_hash))

        with patch('apps.lockers.services.publish_locker_command'):
            retrieve = request_marketplace_retrieval(
                transaction_id=self.purchase.pk,
                buyer=self.buyer,
                otp=otp,
            )
        self.purchase.refresh_from_db()
        self.assertIsNotNone(self.purchase.otp_consumed_at)
        with self.assertRaises(InvalidPickupCode):
            request_marketplace_retrieval(
                transaction_id=self.purchase.pk,
                buyer=self.buyer,
                otp=otp,
            )

        self.send_event(retrieve, IoTEvent.EventType.ITEM_REMOVED)
        self.purchase.refresh_from_db()
        self.market_locker.refresh_from_db()
        self.assertEqual(self.purchase.status, Transaction.TransactionStatus.COMPLETED)
        self.assertEqual(self.purchase.otp_hash, '')
        self.assertEqual(self.market_locker.status, Locker.LockerStatus.AVAILABLE)

    @patch('apps.lockers.services.publish_locker_command')
    def test_wrong_pickup_code_is_limited_to_five_attempts(self, publish):
        command = request_marketplace_deposit(transaction_id=self.purchase.pk, actor=self.seller)
        self.send_event(command, IoTEvent.EventType.ITEM_DETECTED)
        notification = Notification.objects.get(user=self.buyer, title='Locker pickup code')
        correct = re.search(r'Pickup code: (\d{6})', notification.body).group(1)
        wrong = f'{(int(correct) + 1) % 1_000_000:06d}'

        for attempt in range(5):
            expected = PickupCodeLocked if attempt == 4 else InvalidPickupCode
            with self.assertRaises(expected):
                request_marketplace_retrieval(
                    transaction_id=self.purchase.pk,
                    buyer=self.buyer,
                    otp=wrong,
                )

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.otp_failed_attempts, 5)
        with self.assertRaises(PickupCodeLocked):
            request_marketplace_retrieval(
                transaction_id=self.purchase.pk,
                buyer=self.buyer,
                otp=correct,
            )
        publish.assert_called_once()

    @patch('apps.lockers.services.publish_locker_command')
    def test_delivery_requires_inbound_removal_before_storage_and_owner_retrieval(self, publish):
        inbound = request_delivery_inbound(delivery_id=self.delivery.pk, courier=self.courier)
        self.send_event(inbound, IoTEvent.EventType.ITEM_DETECTED)
        self.delivery.refresh_from_db()
        self.inbound_locker.refresh_from_db()
        self.assertEqual(self.delivery.status, Delivery.DeliveryStatus.DEPOSITED)
        self.assertEqual(self.inbound_locker.status, Locker.LockerStatus.OCCUPIED)

        transfer = request_delivery_transfer(delivery_id=self.delivery.pk, courier=self.courier)
        self.send_event(transfer, IoTEvent.EventType.ITEM_REMOVED)
        self.inbound_locker.refresh_from_db()
        self.assertEqual(self.inbound_locker.status, Locker.LockerStatus.AVAILABLE)

        store = request_delivery_storage(delivery_id=self.delivery.pk, courier=self.courier)
        self.send_event(store, IoTEvent.EventType.ITEM_DETECTED)
        self.delivery.refresh_from_db()
        self.storage_locker.refresh_from_db()
        self.assertEqual(self.delivery.status, Delivery.DeliveryStatus.COMPLETED)
        self.assertEqual(self.storage_locker.status, Locker.LockerStatus.OCCUPIED)
        self.assertTrue(Notification.objects.filter(user=self.buyer, title='Delivery stored').exists())

        with self.assertRaises(PermissionError):
            request_delivery_retrieval(delivery_id=self.delivery.pk, owner=self.other_buyer)
        with patch('apps.lockers.services.publish_locker_command'):
            retrieve = request_delivery_retrieval(delivery_id=self.delivery.pk, owner=self.buyer)
        self.send_event(retrieve, IoTEvent.EventType.ITEM_REMOVED)
        self.delivery.refresh_from_db()
        self.storage_locker.refresh_from_db()
        self.assertIsNotNone(self.delivery.retrieved_at)
        self.assertEqual(self.storage_locker.status, Locker.LockerStatus.AVAILABLE)
        self.assertEqual(publish.call_count, 3)

    @patch('apps.lockers.services.publish_locker_command')
    def test_staff_reconciliation_requires_expiry_and_audits_physical_decision(self, publish):
        User = get_user_model()
        admin = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='test-password',
        )
        command = request_marketplace_deposit(transaction_id=self.purchase.pk, actor=self.seller)
        LockerRequest.objects.filter(pk=command.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        call_command('expire_locker_commands', verbosity=0)
        command.refresh_from_db()
        self.assertEqual(command.status, LockerRequest.Status.UNKNOWN)

        command = reconcile_locker_command(
            command_id=command.command_id,
            actor=admin,
            decision='confirm_completed',
            confirmation='DEVICE_CONFIRMED_ITEM_PRESENT',
        )
        self.purchase.refresh_from_db()
        self.market_locker.refresh_from_db()
        self.assertEqual(command.status, LockerRequest.Status.COMPLETED)
        self.assertEqual(self.purchase.status, Transaction.TransactionStatus.AWAITING_PICKUP)
        self.assertEqual(self.market_locker.status, Locker.LockerStatus.OCCUPIED)
        self.assertTrue(
            LockerLog.objects.filter(
                command=command,
                action=LockerLog.Action.RECONCILE,
                user=admin,
                is_successful=True,
            ).exists()
        )
        publish.assert_called_once()

    @patch('apps.lockers.services.publish_locker_command')
    def test_device_event_replay_is_idempotent_and_cross_command_reuse_conflicts(self, publish):
        command = request_marketplace_deposit(transaction_id=self.purchase.pk, actor=self.seller)
        event_id = uuid.uuid4()

        first, duplicate, late = self.send_event(
            command,
            IoTEvent.EventType.LOCKER_OPENED,
            event_id=event_id,
        )
        self.assertFalse(duplicate)
        self.assertFalse(late)
        self.assertEqual(IoTEvent.objects.filter(event_id=event_id).count(), 1)

        replay, duplicate, late = self.send_event(
            command,
            IoTEvent.EventType.LOCKER_OPENED,
            event_id=event_id,
        )
        self.assertTrue(duplicate)
        self.assertFalse(late)
        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(IoTEvent.objects.filter(event_id=event_id).count(), 1)

        # Replaying the same event_id against a different command must be a conflict,
        # not silently accepted as an idempotent duplicate.
        inbound = request_delivery_inbound(delivery_id=self.delivery.pk, courier=self.courier)
        with self.assertRaises(DeviceEventConflict):
            self.send_event(
                inbound,
                IoTEvent.EventType.LOCKER_OPENED,
                event_id=event_id,
            )

        # A redelivered acknowledgement after the command reached a terminal state stays
        # idempotent instead of being rejected as an incompatible event.
        self.send_event(command, IoTEvent.EventType.ITEM_DETECTED)
        command.refresh_from_db()
        self.assertEqual(command.status, LockerRequest.Status.COMPLETED)
        replay_after_completion, duplicate, late = self.send_event(
            command,
            IoTEvent.EventType.LOCKER_OPENED,
            event_id=event_id,
        )
        self.assertTrue(duplicate)
        self.assertFalse(late)
        self.assertEqual(replay_after_completion.pk, first.pk)
        self.assertEqual(IoTEvent.objects.filter(event_id=event_id).count(), 1)
