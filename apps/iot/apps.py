from django.apps import AppConfig


class IotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.iot'

    def ready(self):
        # Import signals for IoT notifications
        try:
            import apps.iot.signals  # noqa: F401
        except Exception:  # pragma: no cover - defensive import
            pass
