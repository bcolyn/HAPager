import time

try:  # only for CPython
    from typing import List, Union, Tuple

except ImportError:
    pass  # ignore the error

import array
import math
import os
import ssl

import adafruit_dotstar as dotstar
import adafruit_minimqtt.adafruit_minimqtt as MQTT
import audiobusio
import audiocore
import board
import digitalio
import keypad
import rotaryio
import socketpool
import wifi

print(f"Connecting to {os.getenv('CIRCUITPY_WIFI_SSID')}")
wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
print(f"Connected to {os.getenv('CIRCUITPY_WIFI_SSID')}!")

root = os.getenv('MQTT_TOPIC_ROOT')

# constants
alert_topic = f"{root}/alerts/+"
heartbeat_topic = f"{root}/heartbeat"

# globals
mqtt_client: MQTT


class ScrollBuffer:
    LINES = 12  # we can show 12 lines of text via stdout
    messages: List[str]
    cursor: int  # the offset we keep versus the last message, 0 < cursor < len(messages)
    encoder: rotaryio.IncrementalEncoder
    last_position: int

    def __init__(self):
        # setup the pins for the rotary encoder
        pin = digitalio.DigitalInOut(board.IO2)
        with pin:
            pin.direction = digitalio.Direction.INPUT
        pin = digitalio.DigitalInOut(board.IO1)
        with pin:
            pin.direction = digitalio.Direction.INPUT
        self.encoder = rotaryio.IncrementalEncoder(board.IO1, board.IO2, divisor=2)
        self.last_position = 0
        self.cursor = 0
        self.messages = list()

    # update ourselves
    def loop(self):
        # check rotary encoder
        position = self.encoder.position
        if position != self.last_position:
            delta = position - self.last_position
            self.cursor = max(0, min(self.cursor + delta, len(self.messages) - self.LINES))
            self.refresh_messages()
            # print(f"len {len(self.messages)} pos {position} last {self.last_position} cur {self.cursor}")
            self.last_position = position

    def init_demo(self):
        self.messages += [
            "W 00:00:00 Line 1",
            "W 00:00:00 Line 2",
            "W 00:00:00 Line 3",
            "W 00:00:00 Line 4",
            "W 00:00:00 Line 5",
            "W 00:00:00 Line 6",
            "W 00:00:00 Line 7",
            "W 00:00:00 Line 8",
            "W 00:00:00 Line 9",
            "W 00:00:00 Line 10",
            "W 00:00:00 Line 11",
            "W 00:00:00 Line 12",
            "W 00:00:00 Line 13",
            "W 00:00:00 Line 14",
            "W 00:00:00 Line 15",
            "W 00:00:00 Line 16",
            "W 00:00:00 Line 17",
            "W 00:00:00 Line 18",
            "W 00:00:00 Line 19",
            "W 00:00:00 Line 20",
        ]
        for message in self.messages:
            print(message)

    def refresh_messages(self):
        start_index = len(self.messages) - self.LINES - self.cursor

        for i in range(start_index, start_index + self.LINES):
            if 0 <= i < len(self.messages):
                line = self.messages[i]
            else:
                line = ""
            print(line)


class AlertManager:
    _blink_ts: float = time.monotonic()
    _level: int = 0
    _effects_on: bool = False

    def __init__(self):
        self.leds = dotstar.DotStar(board.IO45, board.IO42, 7, brightness=0.2)
        self.sine_wave = self._generate_sample()
        self.i2s = audiobusio.I2SOut(board.I2S_BCLK, board.I2S_WCLK, board.I2S_DOUT)

    @staticmethod
    def _generate_sample():
        length = 8000 // 440
        audio_volume = 0.2
        sine_wave = array.array("H", [0] * length)
        for i in range(length):
            sine_wave[i] = int(audio_volume * math.sin(math.pi * 2 * i / length) * (2 ** 15) + 2 ** 15)
        return audiocore.RawSample(sine_wave, sample_rate=8000)

    def set_level(self, level):
        if level == 1:  # WARN
            self.leds.fill((255, 255, 0))
        elif level == 2:  # ERROR
            self.leds.fill((255, 0, 0))
        elif level == 3:  # CRITICAL
            self.leds.fill((255, 0, 0))
        self._level = level

    def stop_sound(self):
        self.i2s.stop()

    def play_sound(self):
        self.i2s.play(self.sine_wave, loop=True)

    def leds_off(self):
        self.leds.brightness = 0

    def leds_on(self):
        self.leds.brightness = 1

    def loop(self):
        if self._level == 0:
            if self._effects_on:
                self.toggle_effects()
        else:
            now_ts = time.monotonic()
            if now_ts > self._blink_ts + 1:
                self._blink_ts = now_ts
                self.toggle_effects()

    def toggle_effects(self):
        if not self._effects_on:
            self.run_effects()
            self._effects_on = True
        else:
            self.stop_sound()
            self.leds_off()
            self._effects_on = False

    def run_effects(self):
        if self._level >= 1:
            self.leds_on()
        if self._level >= 3:
            self.play_sound()

    def get_level(self):
        return self._level

    def raise_level(self, value):
        if self._level < value:
            self.set_level(value)


class DisplayManager:
    keys: keypad.Keys

    def __init__(self, alert_manager: AlertManager):
        self.alertManager = alert_manager
        self.keys = keypad.Keys((board.IO0,), value_when_pressed=True, pull=True)
        self.display = board.DISPLAY

    def loop(self):
        event = self.keys.events.get()
        while event:
            if event.pressed:
                if self.is_display_off():
                    self.display.brightness = 1
                else:
                    self.display.brightness = 0
                    self.alertManager.set_level(0)
            event = self.keys.events.get()

    def is_display_off(self):
        return self.display.brightness == 0.0

    def turn_display_on(self):
        self.display.brightness = 1


# Mqtt Callbacks
def connected(client, userdata, flags, rc):
    # This function will be called when the client is connected
    # successfully to the broker.
    print(f"Connected to broker! Listening for topic changes on {alert_topic}")
    # Subscribe to all changes on the alert_feed.
    client.subscribe(alert_topic)


def disconnected(client: MQTT, userdata, rc):
    # This method is called when the client is disconnected
    print("Disconnected from broker, reconnecting.")
    client.reconnect()


def message(client, topic: str, message):
    # This method is called when a topic the client is subscribed to
    # has a new message.
    # print(f"New message on topic {topic}: {message}")
    if not message:
        alertManager.set_level(0)
        return
    if topic.endswith("warn"):
        line = f"W: {message}"
        alertManager.raise_level(1)
    elif topic.endswith("error"):
        line = f"E: {message}"
        alertManager.raise_level(2)
    elif topic.endswith("critical"):
        line = f"!: {message}"
        alertManager.raise_level(3)
    else:
        line = f"I: {message}"

    scrollBuffer.messages.append(line)
    print(line)
    displayManager.turn_display_on()


def init_mqtt():
    global mqtt_client
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
        socket_timeout=1,
        ssl_context=ssl_context,
    )
    # Setup the callback methods above
    mqtt_client.on_connect = connected
    mqtt_client.on_disconnect = disconnected
    mqtt_client.on_message = message
    # Connect the client to the MQTT broker.
    print("Connecting to broker...")
    mqtt_client.connect()


init_mqtt()

scrollBuffer = ScrollBuffer()
alertManager = AlertManager()
displayManager = DisplayManager(alertManager)

displayManager.turn_display_on()
# scrollbuffer.init_demo()
# alertManager.set_level(2)

last_msg_poll = 0
last_heartbeat = 0
while True:
    now = time.monotonic()
    if displayManager.is_display_off():
        mqtt_client.loop()
        last_msg_poll = now
    else:
        if last_msg_poll + 60 < now:
            mqtt_client.loop()
            last_msg_poll = now
    if last_heartbeat + 10 < now:
        if mqtt_client.is_connected():
            mqtt_client.publish(heartbeat_topic,f"{now}")
            last_heartbeat = now
    scrollBuffer.loop()
    alertManager.loop()
    #
    time.sleep(0.1)
    displayManager.loop()
