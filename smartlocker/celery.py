import os
from django.conf import settings
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartlocker.settings')

app = Celery('smartlocker')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
app.autodiscover_tasks(['apps.lockers', 'apps.notifications', 'apps.iot'])
