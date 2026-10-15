import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.iot.models import IoTEvent
from apps.lockers.models import Locker, LockerRequest
from apps.lockers.services import request_marketplace_deposit
from apps.marketplace.models import Product, Store, Transaction


@override_settings(SMARTLOCKER_DEVICE_TOKEN='device-test-token-that-is-long-enough-123456')
class DeviceEventIngestTests(TestCase):
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
        self.locker = Locker.objects.create(number='M1', type=Locker.LockerType.MARKETPLACE)
        store = Store.objects.create(owner=self.seller, name='Store')
        product = Product.objects.create(
            store=store,
            seller=self.seller,
            name='Item',
            price=Decimal('10.00'),
            stock=1,
            description='Item description',
        )
        self.purchase = Transaction.objects.create(
            buyer=self.buyer,
            seller=self.seller,
            product=product,
            quantity=1,
            total_price=Decimal('10.00'),
            status=Transaction.TransactionStatus.PAYMENT_VERIFIED,
        )
        self.client = APIClient()

    @patch('apps.lockers.services.publish_locker_command')
    def test_device_token_binds_idempotent_events_to_the_command(self, publish):
        command = request_marketplace_deposit(transaction_id=self.purchase.pk, actor=self.seller)
        event_id = uuid.uuid4()
        body = {
            'event_id': str(event_id),
            'command_id': str(command.command_id),
            'event_type': IoTEvent.EventType.LOCKER_OPENED,
            'payload': {},
        }
        url = reverse('iot-events-ingest')

        denied = self.client.post(url, body, format='json', HTTP_X_DEVICE_TOKEN='wrong-token')
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(IoTEvent.objects.count(), 0)

        accepted = self.client.post(
            url,
            body,
            format='json',
            HTTP_X_DEVICE_TOKEN='device-test-token-that-is-long-enough-123456',
        )
        self.assertEqual(accepted.status_code, status.HTTP_201_CREATED, accepted.data)
        self.assertEqual(accepted.data['command_id'], str(command.command_id))
        self.assertEqual(IoTEvent.objects.get(event_id=event_id).command_id, command.pk)

        replay = self.client.post(
            url,
            body,
            format='json',
            HTTP_X_DEVICE_TOKEN='device-test-token-that-is-long-enough-123456',
        )
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(IoTEvent.objects.count(), 1)

        conflicting = dict(body, payload={'different': True})
        conflict_response = self.client.post(
            url,
            conflicting,
            format='json',
            HTTP_X_DEVICE_TOKEN='device-test-token-that-is-long-enough-123456',
        )
        self.assertEqual(conflict_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(LockerRequest.objects.get(pk=command.pk).status, LockerRequest.Status.OPENED)
        publish.assert_called_once()
