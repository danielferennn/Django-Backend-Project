import json
import threading

import paho.mqtt.client as mqtt
from django.conf import settings


class MqttPublishError(Exception):
    pass


def publish_locker_command(command, *, timeout=5):
    if command.locker_id is None or command.expires_at is None:
        raise MqttPublishError('Command is missing its locker or expiry.')

    topic = settings.MQTT_TOPIC_COMMAND.format(locker_number=command.locker.number)
    payload = json.dumps(
        {
            'command_id': str(command.command_id),
            'locker_number': command.locker.number,
            'purpose': command.purpose,
            'action': 'OPEN',
            'expires_at': command.expires_at.isoformat(),
        },
        separators=(',', ':'),
    )
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f'smartlocker-{command.command_id}',
    )
    client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
    if settings.MQTT_TLS:
        client.tls_set(ca_certs=settings.MQTT_CA_CERT)

    connected = threading.Event()
    connection_error = []

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            connection_error.append('broker rejected connection')
        connected.set()

    client.on_connect = on_connect
    loop_started = False
    try:
        client.connect(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, keepalive=10)
        client.loop_start()
        loop_started = True
        if not connected.wait(timeout):
            raise MqttPublishError('Timed out connecting to MQTT broker.')
        if connection_error:
            raise MqttPublishError(connection_error[0])

        info = client.publish(topic, payload, qos=1, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise MqttPublishError('MQTT broker rejected command publication.')
        info.wait_for_publish(timeout=timeout)
        if not info.is_published():
            raise MqttPublishError('Timed out waiting for MQTT command acknowledgement.')
    except MqttPublishError:
        raise
    except Exception as exc:
        raise MqttPublishError('MQTT command publication failed.') from exc
    finally:
        if client.is_connected():
            client.disconnect()
        if loop_started:
            client.loop_stop()
