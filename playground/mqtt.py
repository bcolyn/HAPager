# SPDX-FileCopyrightText: 2021 ladyada for Adafruit Industries
# SPDX-License-Identifier: MIT

import os
import ssl
import time

import socketpool
import wifi

import adafruit_minimqtt.adafruit_minimqtt as MQTT

# Add settings.toml to your filesystem CIRCUITPY_WIFI_SSID and CIRCUITPY_WIFI_PASSWORD keys
# with your WiFi credentials. DO NOT share that file or commit it into Git or other
# source control.

print(f"Connecting to {os.getenv('CIRCUITPY_WIFI_SSID')}")
wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
print(f"Connected to {os.getenv('CIRCUITPY_WIFI_SSID')}!")
### Feeds ###

# Setup a feed named 'onoff' for subscribing to changes
alert_feed = "benny/alerts"

### Code ###


# Define callback methods which are called when events occur
def connected(client, userdata, flags, rc):
    # This function will be called when the client is connected
    # successfully to the broker.
    print(f"Connected to broker! Listening for topic changes on {alert_feed}")
    # Subscribe to all changes on the alert_feed.
    client.subscribe(alert_feed)


def disconnected(client, userdata, rc):
    # This method is called when the client is disconnected
    print("Disconnected from broker")


def message(client, topic, message):
    # This method is called when a topic the client is subscribed to
    # has a new message.
    print(f"New message on topic {topic}: {message}")


# Create a socket pool
pool = socketpool.SocketPool(wifi.radio)
ssl_context = ssl.create_default_context()

# If you need to use certificate/key pair authentication (e.g. X.509), you can load them in the
# ssl context by uncommenting the lines below and adding the following keys to your settings.toml:
# "device_cert_path" - Path to the Device Certificate
# "device_key_path" - Path to the RSA Private Key
# ssl_context.load_cert_chain(
#     certfile=os.getenv("device_cert_path"), keyfile=os.getenv("device_key_path")
# )

# Set up a MiniMQTT Client
mqtt_client = MQTT.MQTT(
    broker=os.getenv('MQTT_HOST'),
    port=os.getenv('MQTT_PORT'),
    username=os.getenv('MQTT_USER'),
    password=os.getenv('MQTT_PASS'),
    socket_pool=pool,
    ssl_context=ssl_context,
)

# Setup the callback methods above
mqtt_client.on_connect = connected
mqtt_client.on_disconnect = disconnected
mqtt_client.on_message = message

# Connect the client to the MQTT broker.
print("Connecting to broker...")
mqtt_client.connect()

while True:
    # Poll the message queue
    mqtt_client.loop(timeout=1000)
    # Send a new message
    # time.sleep(5)
