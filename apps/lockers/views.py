from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iot.models import IoTEvent
from apps.marketplace.models import Transaction
from apps.notifications.tasks import push_notification_task
from apps.users.models import User

from .models import Delivery, Locker, LockerLog
from .permissions import IsCourierUser
from .serializers import LockerLogSerializer, OtpValidationSerializer
from .services import BlynkAPIService
from .tasks import send_notification_task

# Import gpiozero jika tersedia
try:
    from gpiozero import OutputDevice
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("WARNING: gpiozero library not found. GPIO control will be simulated.")


@extend_schema(exclude=True)
class VerifyDeliveryView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsCourierUser]

    def post(self, request, *args, **kwargs):
        receipt_number = request.data.get('receipt_number')
        if not receipt_number:
            return Response({'error': 'Receipt number is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            delivery = Delivery.objects.get(receipt_number=receipt_number, status=Delivery.DeliveryStatus.PENDING)
            inbound_locker = delivery.locker
            
            if inbound_locker.status != Locker.LockerStatus.AVAILABLE:
                return Response({'error': 'Inbound locker is currently occupied.'}, status=status.HTTP_409_CONFLICT)

            # --- LOGIKA BARU: Gunakan GPIO langsung ---
            gpio_pin = inbound_locker.gpio_pin
            if gpio_pin is None:
                raise ValueError(f"GPIO pin for inbound locker {inbound_locker.number} is not configured.")
            
            # Panggil fungsi untuk membuka loker via GPIO
            # Kita perlu membuat instance dari OpenStorageLockerView untuk memanggil metodenya
            # atau memindahkan _trigger_gpio_pin menjadi fungsi helper biasa.
            # Untuk sementara, kita panggil langsung di sini.
            OpenStorageLockerView()._trigger_gpio_pin(gpio_pin)
            # --- AKHIR LOGIKA BARU ---
            
            delivery.status = Delivery.DeliveryStatus.VERIFIED
            delivery.save()

            inbound_locker.status = Locker.LockerStatus.OCCUPIED
            inbound_locker.last_opened_by = request.user
            inbound_locker.save()
            
            LockerLog.objects.create(
                locker=inbound_locker,
                user=request.user,
                action=LockerLog.Action.OPEN,
                details=f"Courier verified receipt: {receipt_number}"
            )
            
            return Response({'message': 'Receipt verified. Locker is open.'}, status=status.HTTP_200_OK)

        except Delivery.DoesNotExist:
            return Response({'error': 'Invalid or already processed receipt number.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@extend_schema(exclude=True)
class ConfirmDepositWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            delivery = Delivery.objects.get(status=Delivery.DeliveryStatus.VERIFIED)
            inbound_locker = delivery.locker
            storage_locker = Locker.objects.filter(type=Locker.LockerType.STORAGE, status=Locker.LockerStatus.AVAILABLE).first()
            if not storage_locker:
                return Response({'error': 'No available storage locker.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            delivery.status = Delivery.DeliveryStatus.COMPLETED
            delivery.save()
            
            inbound_locker.status = Locker.LockerStatus.AVAILABLE
            inbound_locker.save()
            
            storage_locker.status = Locker.LockerStatus.OCCUPIED
            storage_locker.save()

            LockerLog.objects.create(locker=inbound_locker, action=LockerLog.Action.DEPOSIT, details="Item moved to storage locker.")
            LockerLog.objects.create(locker=storage_locker, action=LockerLog.Action.DEPOSIT, details=f"Item from receipt {delivery.receipt_number} stored.")

            owner_user_id = 1 
            send_notification_task.delay(
                user_id=owner_user_id, 
                message=f"Barang Anda dengan resi {delivery.receipt_number} telah berhasil disimpan."
            )
            return Response({'message': 'Deposit confirmed.'}, status=status.HTTP_200_OK)
        except Delivery.DoesNotExist:
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@extend_schema(exclude=True)
class OpenStorageLockerView(APIView):
    """
    Endpoint untuk membuka loker penyimpanan (1, 2, atau 3) secara langsung
    melalui pin GPIO Raspberry Pi menggunakan gpiozero.
    """
    permission_classes = [permissions.IsAuthenticated]
    VALID_SLOTS = {'1', '2', '3'} # Asumsi nomor loker di DB adalah string '1', '2', '3'

    def post(self, request, locker_slot=None, *args, **kwargs):
        requested_slot = locker_slot
        if requested_slot is None:
            requested_slot = request.data.get('locker_slot')
        if requested_slot is None:
            requested_slot = request.data.get('locker_number')

        try:
            slot_value = str(requested_slot)
        except (TypeError, ValueError):
            return Response({'error': 'locker_slot must be provided.'}, status=status.HTTP_400_BAD_REQUEST)

        if slot_value not in self.VALID_SLOTS:
            return Response({'error': f'locker_slot must be one of {self.VALID_SLOTS}.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # 1. Cari loker di database berdasarkan nomornya
            locker_to_open = Locker.objects.get(number=slot_value, type=Locker.LockerType.STORAGE)
            
            # 2. Dapatkan nomor pin GPIO dari database
            gpio_pin = locker_to_open.gpio_pin # Menggunakan field gpio_pin yang baru
            if gpio_pin is None:
                raise ValueError(f"GPIO pin for locker {slot_value} is not configured in the database.")

            # 3. Panggil fungsi untuk membuka loker via GPIO
            self._trigger_gpio_pin(gpio_pin)

            # 4. Catat aktivitas di log
            LockerLog.objects.create(
                locker=locker_to_open,
                user=request.user,
                action=LockerLog.Action.OPEN,
                details=f"Locker {slot_value} opened directly by user {request.user.email} via GPIO pin {gpio_pin}."
            )

            return Response({
                'status': 'success',
                'message': f'Locker {slot_value} opened.',
            }, status=status.HTTP_200_OK)

        except Locker.DoesNotExist:
            return Response({'error': f'Storage locker number {slot_value} not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': f'An error occurred: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _trigger_gpio_pin(self, pin_number):
        """
        Fungsi untuk mengirim sinyal ke pin GPIO spesifik menggunakan gpiozero.
        """
        if GPIO_AVAILABLE:
            try:
                device = OutputDevice(pin_number, active_high=True, initial_value=False)
                device.on()
                import time
                time.sleep(1)
                device.off()
            except Exception as e:
                print(f"ERROR: Gagal mengontrol GPIO pin {pin_number} dengan gpiozero: {e}")
                raise e
        else:
            print("--- SIMULASI GPIO ---")
            print(f"gpiozero library tidak ditemukan.")
            print(f"Mengirim sinyal HIGH ke pin {pin_number} selama 1 detik (simulasi).")
            print("--- AKHIR SIMULASI ---")


class LockerLogListView(generics.ListAPIView):
    serializer_class = LockerLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = LockerLog.objects.select_related('locker', 'user')
        user = self.request.user
        if getattr(user, 'is_staff', False):
            return queryset
        role = getattr(user, 'role', None)
        if role == User.Role.OWNER:
            return queryset.filter(locker__last_opened_by=user)
        return queryset.filter(user=user)


def _require_device_token(request):
    expected = getattr(settings, 'SMARTLOCKER_DEVICE_TOKEN', None)
    if not expected:
        return
    provided = request.headers.get('X-Device-Token')
    if provided != expected:
        raise PermissionDenied('Invalid device token')


class ValidateOtpView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=OtpValidationSerializer, responses={'200': OpenApiResponse(description='OTP validation success')})
    def post(self, request, *args, **kwargs):
        _require_device_token(request)
        serializer = OtpValidationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp_code = serializer.validated_data['otp']

        transaction = Transaction.objects.filter(otp=otp_code).order_by('-updated_at').first()
        if not transaction:
            return Response({'status': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)

        if transaction.status != Transaction.TransactionStatus.RELEASED:
            transaction.status = Transaction.TransactionStatus.RELEASED
            transaction.save(update_fields=['status', 'updated_at'])

        event = IoTEvent.objects.create(
            user=transaction.buyer,
            event_type=IoTEvent.EventType.DEVICE,
            payload={'event': 'otp_validated', 'transaction_id': transaction.id},
        )

        push_notification_task(
            user_ids=[transaction.seller_id],
            title='OTP Validated',
            body=f'OTP {otp_code} telah divalidasi. Locker siap dibuka.'
        )

        if transaction.buyer_id:
            push_notification_task(
                user_ids=[transaction.buyer_id],
                title='OTP Digunakan',
                body='Kode OTP Anda digunakan untuk membuka locker.'
            )

        return Response({
            'status': 'ok',
            'transaction_id': transaction.id,
            'event_id': event.id,
        }, status=status.HTTP_200_OK)