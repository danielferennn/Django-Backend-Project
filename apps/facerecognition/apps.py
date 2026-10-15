import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class FacerecognitionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.facerecognition'

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_save, pre_save

        user_model = get_user_model()
        pre_save.connect(
            _stash_owner_eligibility,
            sender=user_model,
            dispatch_uid='facerecognition.stash_owner_eligibility',
        )
        post_save.connect(
            reconcile_face_model_on_owner_change,
            sender=user_model,
            dispatch_uid='facerecognition.reconcile_face_model_on_owner_change',
        )


def _owner_eligible(user):
    from apps.users.models import User

    return bool(user.is_active and user.role == User.Role.OWNER)


def _stash_owner_eligibility(sender, instance, update_fields=None, **kwargs):
    """Remember the pre-save OWNER+active eligibility on the instance.

    A partial save that cannot touch ``role``/``is_active`` (e.g. a login
    ``last_login`` update) skips the extra query and marks the previous state
    unknown, so :func:`reconcile_face_model_on_owner_change` does nothing.
    """
    if instance.pk is None:
        instance._face_eligible_before = None
        return
    if update_fields is not None and not ({'role', 'is_active'} & set(update_fields)):
        instance._face_eligible_before = None
        return
    from apps.users.models import User

    row = User.objects.filter(pk=instance.pk).values_list('role', 'is_active').first()
    instance._face_eligible_before = (
        None if row is None else (row[0] == User.Role.OWNER and row[1])
    )


def reconcile_face_model_on_owner_change(sender, instance, created, raw=False, **kwargs):
    """Rebuild/remove the model when a user's OWNER eligibility actually changes.

    Recognition already fails closed for inactive/downgraded owners, but the
    trained model on disk would otherwise keep a stale label until the next
    enrolment/removal.  This reconciles on a transition in *either* direction
    (active OWNER -> ineligible, or back) and only when the user has private
    training records, so unrelated profile edits never trigger a rebuild.

    The rebuild is deferred via ``transaction.on_commit`` and isolated from
    rebuild failures, so it never runs inside the save transaction nor breaks
    the user save; recognition stays fail-closed regardless.
    """
    if raw or created:
        return
    eligible_before = getattr(instance, '_face_eligible_before', None)
    eligible_after = _owner_eligible(instance)
    if eligible_before is None or eligible_before == eligible_after:
        return

    from .models import Datawajahnew

    if not Datawajahnew.objects.filter(user=instance).exists():
        return

    from django.db import transaction

    from .services import reconcile_face_model_for_user

    def _reconcile():
        try:
            reconcile_face_model_for_user(instance)
        except Exception:
            # Runs on commit: after the user save has already been committed, so
            # no rebuild failure (OpenCV, OSError/PermissionError on the lock,
            # mkdir or os.replace, or ModelPermissionError) may propagate and
            # turn a successful save into a 500.  Recognition stays fail-closed
            # because the label map excludes ineligible owners regardless.
            logger.exception(
                'Could not rebuild the biometric model after user %s eligibility changed.',
                instance.pk,
            )

    transaction.on_commit(_reconcile)
