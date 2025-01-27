import asyncio
import os
import ssl

import adafruit_dotstar as dotstar
import adafruit_minimqtt.adafruit_minimqtt as MQTT
import board
import socketpool
import wifi

alert_feed = "benny/alerts"


class State:
    leds_on: bool = False
    leds_color = (0, 255, 0)


state = State()


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
    state.leds_on = True


async def blink(state_ref: State, interval):  # Don't forget the async!
    with dotstar.DotStar(board.IO45, board.IO42, 7) as leds:
        while True:
            if state_ref.leds_on:
                print("ON")
                leds.fill(state.leds_color)
                leds.brightness = 1
                await asyncio.sleep(interval)
            print("OFF")
            leds.brightness = 0
            await asyncio.sleep(interval)


async def message_poll(mqtt_client: MQTT):
    while True:
        mqtt_client.loop()
        await asyncio.sleep(100)


async def main():  # Don't forget the async!
    led_task = asyncio.create_task(blink(state, 5))
    msg_task = asyncio.create_task(message_poll(mqtt_client))
    await asyncio.gather(led_task, msg_task)
    print("done")


print(f"Connecting to {os.getenv('CIRCUITPY_WIFI_SSID')}")
wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
print(f"Connected to {os.getenv('CIRCUITPY_WIFI_SSID')}!")

pool = socketpool.SocketPool(wifi.radio)
ssl_context = ssl.create_default_context()

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

asyncio.run(main())
