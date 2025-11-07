import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


class ApiLatencyTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            email='latency@example.com', username='latency', password='testpass123'
        )
        self.client = APIClient()
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def _assert_latency(self, method: str, url: str, max_ms: float = 500.0):
        start = time.perf_counter()
        response = getattr(self.client, method)(url)
        elapsed = (time.perf_counter() - start) * 1000
        self.assertLess(
            elapsed,
            max_ms,
            msg=f"{url} responded in {elapsed:.2f}ms (>= {max_ms}ms)",
        )
        self.assertIn(response.status_code, (200, 204))

    def test_notifications_latency(self):
        self._assert_latency('get', '/api/v1/notifications/')

    def test_transactions_latency(self):
        self._assert_latency('get', '/api/v1/marketplace/transactions/')

    def test_locker_logs_latency(self):
        self._assert_latency('get', '/api/v1/lockers/logs/')
