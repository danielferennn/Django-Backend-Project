from django.contrib import admin

# Register your models here.
from .models import Locker, LockerLog, Delivery, Package # Import model-model Anda                                                                            
                                                                                 
# Daftarkan model Anda di sini.                                                 
admin.site.register(Locker)                                                     
admin.site.register(LockerLog)                                                  
admin.site.register(Delivery)                                                   
admin.site.register(Package)