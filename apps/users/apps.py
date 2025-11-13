from django.apps import AppConfig

# name sebelumnya yang dikomen, yg baru ambil dari backend yg diubah mas fahmi

class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'
    # name = 'apps.users'
