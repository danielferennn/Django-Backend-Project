from django.core.management.base import BaseCommand
from apps.lockers.models import Delivery, Locker
# Impor model lain yang mungkin Anda perlukan, contoh: from apps.marketplace.models import Transaction

class Command(BaseCommand):
    help = 'Scans a receipt code, finds the corresponding delivery/transaction, and can trigger locker actions.'

    def add_arguments(self, parser):
        # Menambahkan argumen jika Anda ingin menjalankannya dengan parameter
        # Contoh: python manage.py scan_receipt <nomor_resi>
        parser.add_argument('receipt_code', type=str, help='The receipt code to process.')

    def handle(self, *args, **options):
        receipt_code = options['receipt_code']
        self.stdout.write(self.style.SUCCESS(f"Processing receipt code: {receipt_code}"))

        try:
            # --- LOGIKA UTAMA ANDA DIMULAI DI SINI ---

            # 1. Cari pengiriman (Delivery) berdasarkan nomor resi (awb).
            #    Asumsi model Delivery Anda memiliki field seperti 'awb' untuk nomor resi.
            #    Jika nama fieldnya berbeda, silakan sesuaikan.
            delivery = Delivery.objects.filter(awb=receipt_code).first()

            if delivery:
                self.stdout.write(self.style.SUCCESS(f"Found Delivery ID: {delivery.id} for Locker: {delivery.locker.label}"))
                
                # 2. Lakukan tindakan selanjutnya.
                #    Contoh: Buka loker yang bersangkutan.
                #    (Ini hanya contoh, logika spesifik tergantung kebutuhan Anda)
                locker = delivery.locker
                if not locker.is_open:
                    self.stdout.write(f"Triggering opening for locker {locker.label}...")
                    # Di sini Anda akan menambahkan kode untuk berinteraksi dengan hardware IoT
                    # locker.open() -> Anda mungkin perlu membuat method ini di model Locker
                else:
                    self.stdout.write(self.style.WARNING(f"Locker {locker.label} is already open."))

            else:
                # Jika tidak ditemukan di Delivery, mungkin ini adalah transaksi marketplace?
                # Anda bisa menambahkan logika pencarian di model lain, misalnya Transaction.
                # transaction = Transaction.objects.filter(shipping_code=receipt_code).first()
                # if transaction:
                #     ...
                self.stdout.write(self.style.ERROR("Receipt code not found in any delivery or transaction."))


            # --- AKHIR DARI LOGIKA UTAMA ---

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"An error occurred: {e}"))
