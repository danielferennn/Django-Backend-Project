from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.test import APIClient

from apps.marketplace.models import Product, Store, Transaction
from apps.package_center.models import PackageEntry


@dataclass
class EndpointCheck:
    name: str
    method: str
    path: str
    client: APIClient


class Command(BaseCommand):
    help = (
        "Scan critical API endpoints used by the mobile app to detect 404/permission errors. "
        "Creates lightweight diagnostic data if required."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--owner-email",
            default="diag-owner@example.com",
            help="Email used for the diagnostic owner/receiver account.",
        )
        parser.add_argument(
            "--buyer-email",
            default="diag-buyer@example.com",
            help="Email used for the diagnostic buyer account.",
        )
        parser.add_argument(
            "--seller-email",
            default="diag-seller@example.com",
            help="Email used for the diagnostic seller account.",
        )

    def handle(self, *args, **options):
        owner = self._get_or_create_user(
            email=options["owner_email"],
            username="diag-owner",
            role="OWNER",
        )
        buyer = self._get_or_create_user(
            email=options["buyer_email"],
            username="diag-buyer",
            role="BUYER",
        )
        seller = self._get_or_create_user(
            email=options["seller_email"],
            username="diag-seller",
            role="SELLER",
        )

        owner_client = APIClient()
        owner_client.force_authenticate(user=owner)

        buyer_client = APIClient()
        buyer_client.force_authenticate(user=buyer)

        seller_client = APIClient()
        seller_client.force_authenticate(user=seller)

        self._ensure_sample_package(owner)
        self._ensure_sample_store_and_transaction(buyer, seller)

        checks: Iterable[EndpointCheck] = [
            EndpointCheck(
                name="Receiver dashboard packages",
                method="get",
                path="/api/v1/package-center/packages/",
                client=owner_client,
            ),
            EndpointCheck(
                name="Package center listing",
                method="get",
                path="/api/v1/package-center/packages/?status=REGISTERED",
                client=owner_client,
            ),
            EndpointCheck(
                name="Package history listing",
                method="get",
                path="/api/v1/package-center/packages/?status=COMPLETED",
                client=owner_client,
            ),
            EndpointCheck(
                name="Buyer store list",
                method="get",
                path="/api/v1/marketplace/stores/",
                client=buyer_client,
            ),
            EndpointCheck(
                name="Seller payment verification list",
                method="get",
                path="/api/v1/marketplace/transactions/?role=seller",
                client=seller_client,
            ),
        ]

        self.stdout.write(self.style.NOTICE("Running endpoint scan..."))
        failures = 0

        for check in checks:
            response = getattr(check.client, check.method.lower())(check.path)
            status_code = response.status_code
            if status_code >= 400:
                failures += 1
                detail = getattr(response, "data", response.content)
                self.stdout.write(
                    self.style.ERROR(
                        f"[{status_code}] {check.name} -> {check.path}\n    Detail: {detail}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[{status_code}] {check.name} -> {check.path}"
                    )
                )

        if failures:
            raise SystemExit(f"Scan finished with {failures} failing endpoint(s).")

        self.stdout.write(self.style.SUCCESS("All scanned endpoints responded successfully."))

    def _get_or_create_user(self, *, email: str, username: str, role: str):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": username,
                "role": role,
            },
        )
        if created:
            user.set_password("PenlokDiag123!")
            user.save(update_fields=["password"])
        elif user.role != role:
            user.role = role
            user.save(update_fields=["role"])
        return user

    def _ensure_sample_package(self, owner):
        PackageEntry.objects.get_or_create(
            owner=owner,
            tracking_number="DIAG-TRACK-001",
            defaults={
                "package_name": "Diagnostic Package",
                "courier": "DiagCourier",
            },
        )

    def _ensure_sample_store_and_transaction(self, buyer, seller):
        store, _ = Store.objects.get_or_create(
            owner=seller,
            defaults={
                "name": "Diagnostic Store",
                "location": "Jakarta",
            },
        )
        product, _ = Product.objects.get_or_create(
            store=store,
            name="Diagnostic Product",
            defaults={
                "price": 100000,
                "stock": 10,
                "description": "Sample data for endpoint scan",
            },
        )
        Transaction.objects.get_or_create(
            buyer=buyer,
            seller=seller,
            product=product,
            quantity=1,
            total_price=product.price,
            defaults={
                "status": Transaction.TransactionStatus.NEED_VERIFICATION,
                "buyer_full_name": "Diagnostic Buyer",
                "shipping_address": "Jakarta",
                "buyer_phone_number": "0800000000",
                "payment_proof_uploaded_at": timezone.now(),
            },
        )
