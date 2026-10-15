from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Notification


class NotificationPushAuthorizationTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.recipient = User.objects.create_user(
            username='recipient', email='recipient@example.test', password='safe-password',
        )
        self.staff = User.objects.create_user(
            username='staff', email='staff@example.test', password='safe-password', is_staff=True,
        )
        self.url = reverse('notifications-push')

    def test_nonstaff_cannot_send_notifications_to_other_users(self):
        self.client.force_authenticate(self.recipient)
        response = self.client.post(self.url, {
            'user_ids': [self.recipient.pk], 'title': 'Locker update', 'body': 'Ready',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Notification.objects.exists())

    def test_staff_push_deduplicates_valid_recipients(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(self.url, {
            'user_ids': [self.recipient.pk, self.recipient.pk],
            'title': 'Locker update',
            'body': 'Ready',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Notification.objects.filter(user=self.recipient).count(), 1)

    def test_invalid_recipient_rejects_the_entire_batch(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(self.url, {
            'user_ids': [self.recipient.pk, 999999], 'title': 'Locker update', 'body': 'Ready',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Notification.objects.exists())

    def test_recipient_batch_is_bounded(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(self.url, {
            'user_ids': list(range(1, 102)), 'title': 'Locker update', 'body': 'Ready',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Notification.objects.exists())
