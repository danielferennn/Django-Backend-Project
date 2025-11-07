from celery import shared_task


@shared_task
def noop_iot_task():
    return 'iot ready'
