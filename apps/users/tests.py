from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .admin import SmartLockerUserAdmin
from .models import User


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class UserRegistrationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('user_registration')

    def test_public_registration_rejects_every_privileged_role(self):
        for role in (User.Role.OWNER, User.Role.SELLER, User.Role.COURIER, User.Role.ADMIN):
            response = self.client.post(self.url, {
                'username': f'user-{role.lower()}',
                'email': f'{role.lower()}@example.test',
                'password': 'Valid-Orange-Maple-2026!',
                'role': role,
            }, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.count(), 0)

    def test_public_registration_validates_password_and_creates_only_buyer(self):
        weak = self.client.post(self.url, {
            'username': 'weak-user',
            'email': 'weak@example.test',
            'password': 'password',
        }, format='json')
        self.assertEqual(weak.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', weak.data)
        self.assertFalse(User.objects.filter(username='weak-user').exists())

        response = self.client.post(self.url, {
            'username': 'buyer-user',
            'email': 'buyer@example.test',
            'password': 'Valid-Orange-Maple-2026!',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username='buyer-user')
        self.assertEqual(user.role, User.Role.BUYER)
        self.assertTrue(user.check_password('Valid-Orange-Maple-2026!'))
        self.assertNotIn('face_id', response.data['user'])

    def test_registration_rejects_unrecognized_identity_fields(self):
        response = self.client.post(self.url, {
            'username': 'buyer-user',
            'email': 'buyer@example.test',
            'password': 'Valid-Orange-Maple-2026!',
            'is_staff': True,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    def test_registration_rate_limit_applies_per_remote_address(self):
        for index in range(10):
            response = self.client.post(self.url, {
                'username': f'blocked-{index}',
                'email': f'blocked-{index}@example.test',
                'password': 'Valid-Orange-Maple-2026!',
                'role': User.Role.OWNER,
            }, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(self.url, {
            'username': 'blocked-last',
            'email': 'blocked-last@example.test',
            'password': 'Valid-Orange-Maple-2026!',
            'role': User.Role.OWNER,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_login_rate_limit_applies_per_remote_address(self):
        login_url = reverse('token_obtain_pair')
        for _ in range(10):
            response = self.client.post(login_url, {'email': 'missing@example.test', 'password': 'wrong'}, format='json')
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        response = self.client.post(
            login_url, {'email': 'missing@example.test', 'password': 'wrong'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class UserFaceIdScopeTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner-a', email='owner-a@example.test', password='safe-password',
            role=User.Role.OWNER, first_name='Owner', last_name='A',
        )
        self.other_owner = User.objects.create_user(
            username='owner-b', email='owner-b@example.test', password='safe-password',
            role=User.Role.OWNER, first_name='Owner', last_name='B',
        )
        self.url = reverse('user_getdatafaceid')

    def test_user_can_lookup_only_own_face_id(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(
            self.client.get(self.url, {'faceid': self.owner.face_id}).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.get(self.url, {'faceid': self.other_owner.face_id}).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_face_id_lookup_requires_authentication_and_parameter(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_400_BAD_REQUEST)


class UserRolePersistenceTests(TestCase):
    def test_role_change_does_not_erase_names_or_face_id(self):
        owner = User.objects.create_user(
            username='owner', email='owner@example.test', password='safe-password',
            role=User.Role.OWNER, first_name='Ari', last_name='Wibowo',
        )
        face_id = owner.face_id
        owner.role = User.Role.BUYER
        owner.save(update_fields=['role'])
        owner.refresh_from_db()
        self.assertEqual((owner.first_name, owner.last_name, owner.face_id), ('Ari', 'Wibowo', face_id))

    def test_owner_requires_both_names_when_model_validates(self):
        user = User(username='owner', email='owner@example.test', role=User.Role.OWNER)
        with self.assertRaises(DjangoValidationError):
            user.full_clean()


class UserAdminProvisioningTests(TestCase):
    def setUp(self):
        self.admin = SmartLockerUserAdmin(User, admin.site)
        self.request = RequestFactory().post('/admin/users/user/')
        self.request.user = User.objects.create_user(
            username='delegated', email='delegated@example.test', password='safe-password',
            is_staff=True, is_superuser=False,
        )
        self.request.user.user_permissions.add(
            Permission.objects.get(content_type__app_label='users', codename='change_user'),
            Permission.objects.get(content_type__app_label='users', codename='add_user'),
        )

    def test_delegated_staff_can_provision_owner_without_staff_privileges(self):
        self.client.force_login(self.request.user)
        response = self.client.post('/admin/users/user/add/', {
            'username': 'provisioned-owner',
            'email': 'provisioned-owner@example.test',
            'password1': 'Violet-River-Plan-27!secure',
            'password2': 'Violet-River-Plan-27!secure',
            'first_name': 'Provisioned',
            'last_name': 'Owner',
            'role': User.Role.OWNER,
            'is_active': 'on',
            '_save': 'Save',
        })
        self.assertEqual(response.status_code, status.HTTP_302_FOUND, getattr(response, 'context', None))
        owner = User.objects.get(email='provisioned-owner@example.test')
        self.assertEqual(owner.role, User.Role.OWNER)
        self.assertEqual((owner.first_name, owner.last_name), ('Provisioned', 'Owner'))
        self.assertFalse(owner.is_staff)
        self.assertFalse(owner.is_superuser)

    def test_delegated_staff_cannot_set_privilege_bits_or_permissions(self):
        target = User.objects.create_user(
            username='buyer', email='buyer@example.test', password='safe-password', role=User.Role.BUYER,
        )
        fieldsets = self.admin.get_fieldsets(self.request, target)
        fields = {field for _, options in fieldsets for field in options['fields']}
        self.assertFalse(fields & {'is_staff', 'is_superuser', 'groups', 'user_permissions'})

        target.role = User.Role.SELLER
        target.is_staff = True
        target.is_superuser = True
        self.admin.save_model(self.request, target, form=None, change=True)
        target.refresh_from_db()
        self.assertEqual(target.role, User.Role.SELLER)
        self.assertFalse(target.is_staff)
        self.assertFalse(target.is_superuser)

    def test_delegated_staff_cannot_change_staff_or_superuser_accounts(self):
        privileged = User.objects.create_user(
            username='privileged', email='privileged@example.test', password='safe-password',
            is_staff=True, is_superuser=True,
        )
        self.assertFalse(self.admin.has_change_permission(self.request, privileged))
        self.assertNotIn(privileged.pk, self.admin.get_queryset(self.request).values_list('pk', flat=True))

    def test_direct_admin_posts_cannot_promote_or_reset_privileged_accounts(self):
        target = User.objects.create_user(
            username='ordinary', email='ordinary@example.test', password='original-password',
        )
        self.client.force_login(self.request.user)
        change_url = f'/admin/users/user/{target.pk}/change/'
        response = self.client.post(change_url, {
            'username': target.username,
            'email': target.email,
            'first_name': '',
            'last_name': '',
            'role': User.Role.SELLER,
            'is_active': 'on',
            'is_staff': 'on',
            'is_superuser': 'on',
            '_save': 'Save',
        })
        self.assertIn(response.status_code, (status.HTTP_200_OK, status.HTTP_302_FOUND))
        target.refresh_from_db()
        self.assertEqual(target.role, User.Role.SELLER)
        self.assertFalse(target.is_staff)
        self.assertFalse(target.is_superuser)

        privileged = User.objects.create_user(
            username='privileged', email='privileged@example.test', password='original-password',
            is_staff=True, is_superuser=True,
        )
        password_url = f'/admin/users/user/{privileged.pk}/password/'
        response = self.client.post(password_url, {
            'password1': 'Attacker-Reset-2026!',
            'password2': 'Attacker-Reset-2026!',
        })
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        privileged.refresh_from_db()
        self.assertTrue(privileged.check_password('original-password'))
