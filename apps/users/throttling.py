from rest_framework.throttling import SimpleRateThrottle


class RemoteAddressThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        ident = request.META.get('REMOTE_ADDR') or 'unknown'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class UserLoginThrottle(RemoteAddressThrottle):
    scope = 'user_login'


class UserRegistrationThrottle(RemoteAddressThrottle):
    scope = 'user_registration'
