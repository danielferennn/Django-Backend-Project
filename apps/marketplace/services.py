from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import uuid

class PaymentGatewayService:
    def create_payment(self, transaction_id, amount, customer_details):
        if settings.DEBUG:
            print("--- MOCK PAYMENT GATEWAY ---")
            print(f"Creating payment for Transaction ID: {transaction_id}")
            print(f"Amount: {amount}")
            print(f"Customer: {customer_details}")
            print("----------------------------")
            expires_at = timezone.now() + timedelta(minutes=30)
            cents = int(float(amount) * 100)
            payload = (
                f"0002010102120216COM.SMARTLOCKER0115TRX{transaction_id:06d}"
                f"5204000053033605408{cents:012d}5802ID"
                f"5907SMARTLK6009JAKARTA62070503QRIS6304"
            )
            reference = uuid.uuid4().hex
            return True, {
                "payment_url": f"https://mock-payment.com/pay/{transaction_id}",
                "qris_payload": payload,
                "expires_at": expires_at,
                "payment_reference": reference,
            }
        return False, {"error": "Not implemented"}

    def release_escrow(self, transaction_id):
        if settings.DEBUG:
            print("--- MOCK PAYMENT GATEWAY ---")
            print(f"Releasing escrow for Transaction ID: {transaction_id}")
            print("----------------------------")
            return True, "Escrow released successfully."
        return False, "Not implemented"
