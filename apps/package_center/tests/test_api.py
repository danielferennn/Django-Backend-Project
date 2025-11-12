from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.package_center.models import PackageEntry


class PackageEntryAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='owner@example.com',
            username='owner',
            password='testpass123',
            role=user_model.ROLE_OWNER,
        )

    def test_owner_can_create_package_with_datetime_strings(self):
        self.client.force_authenticate(user=self.user)
        url = reverse('package-entry-list')
        payload = {
            'package_name': 'Owner Package',
            'tracking_number': 'TRK-123',
            'order_date': '2025-11-12T13:33:50.213Z',
            'delivered_date': '2025-11-13T08:00:00+00:00',
            'receiver_name': 'Owner Receiver',
            'receiver_phone': '08123456789',
            'status': 'REGISTERED',
        }

        response = self.client.post(url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        entry = PackageEntry.objects.get(pk=response.data['id'])
        self.assertEqual(entry.order_date.isoformat(), '2025-11-12')
        self.assertEqual(entry.delivered_date.isoformat(), '2025-11-13')

    def test_owner_can_update_package_status_and_fields(self):
        package = PackageEntry.objects.create(
            owner=self.user,
            package_name='Initial',
            tracking_number='TRK-456',
            status=PackageEntry.Status.REGISTERED,
        )
        self.client.force_authenticate(user=self.user)
        url = reverse('package-entry-detail', args=[package.id])
        payload = {
            'status': PackageEntry.Status.IN_TRANSIT,
            'receiver_name': 'Updated Receiver',
            'locker_slot': 'B-02',
        }
        response = self.client.patch(url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        package.refresh_from_db()
        self.assertEqual(package.status, PackageEntry.Status.IN_TRANSIT)
        self.assertEqual(package.receiver_name, 'Updated Receiver')
        self.assertEqual(package.locker_slot, 'B-02')

    def test_owner_can_delete_package(self):
        package = PackageEntry.objects.create(
            owner=self.user,
            package_name='Delete Me',
            tracking_number='TRK-789',
        )
        self.client.force_authenticate(user=self.user)
        url = reverse('package-entry-detail', args=[package.id])

        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PackageEntry.objects.filter(id=package.id).exists())
