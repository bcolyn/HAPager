"""Home automation pager for the LILYGO T-Embed (ESP32-S3), CircuitPython 10.

Everything runs in one cooperative loop. Two rules keep it responsive:

  * MQTT is polled every tick with a very short socket timeout, so network I/O
    can never stall the loop for more than SOCKET_TIMEOUT.
  * Every other subsystem is a Task with its own period and deadline, so the
    LEDs, the buzzer, the encoder and the button no longer share one clock.
"""

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

# --------------------------------------------------------------------- tuning

# Granularity of a single recv on the MQTT socket. This is the worst case time
# one mqtt loop() call can stall the scheduler, so keep it small.
SOCKET_TIMEOUT = 0.05
# Budget for assembling one complete MQTT message. minimqtt requires this to be
# strictly greater than SOCKET_TIMEOUT.
RECV_TIMEOUT = 1.0
MQTT_KEEP_ALIVE = 60

HEARTBEAT_PERIOD = 10.0
RECONNECT_BACKOFF_MIN = 1.0
RECONNECT_BACKOFF_MAX = 30.0
# how long the broker has to stay gone before we admit it on the LEDs
LINK_DOWN_GRACE = 20.0

LED_BRIGHTNESS = 0.2
LED_OFF = (0, 0, 0)
# keeping the buffer bounded matters more than history on a device this small
MAX_MESSAGES = 100

# alert levels
LEVEL_INFO = 0
LEVEL_WARN = 1
LEVEL_ERROR = 2
LEVEL_CRITICAL = 3

# level -> (colour, seconds lit, seconds dark) - higher levels blink faster
LED_PATTERNS = {
    LEVEL_WARN: ((255, 255, 0), 1.0, 1.0),
    LEVEL_ERROR: ((255, 0, 0), 0.5, 0.5),
    LEVEL_CRITICAL: ((255, 0, 0), 0.25, 0.25),
}
# shown when there is no alert but we cannot reach the broker - a pager that
# lost its broker otherwise looks exactly like a pager with nothing to say
LED_LINK_DOWN = ((0, 0, 255), 0.1, 2.9)

# level -> (seconds on, seconds off) on its own clock; only critical makes noise
BUZZER_PATTERNS = {
    LEVEL_CRITICAL: (0.2, 0.8),
}


class Task:
    """One periodic job in the cooperative scheduler."""

    def __init__(self, fn, period: float):
        self.fn = fn
        self.period = period
        self.next_ts = 0.0

    def maybe_run(self, now: float):
        if now < self.next_ts:
            return
        # deadline measured from now rather than from the previous deadline, so
        # a late tick never causes a burst of catch-up runs
        self.next_ts = now + self.period
        self.fn(now)


class AlertState:
    """The alert level, plus whether we still have a broker."""

    def __init__(self):
        self._level = LEVEL_INFO
        self._link_down_since = None

    def get_level(self) -> int:
        return self._level

    def raise_level(self, value: int):
        if self._level < value:
            self._level = value

    def clear(self):
        self._level = LEVEL_INFO

    def link_down(self, now: float):
        if self._link_down_since is None:
            self._link_down_since = now

    def link_up(self):
        self._link_down_since = None

    def link_is_down(self, now: float) -> bool:
        return self._link_down_since is not None and now - self._link_down_since > LINK_DOWN_GRACE


class LedBlinker:
    """Blinks the DotStar strip on its own clock, independent of the buzzer."""

    def __init__(self, state: AlertState):
        self.state = state
        self.leds = dotstar.DotStar(
            board.IO45, board.IO42, 7, brightness=LED_BRIGHTNESS, auto_write=False
        )
        self.leds.fill(LED_OFF)
        self.leds.show()
        self._pattern = None
        self._lit = False
        self._next_ts = 0.0

    def _current_pattern(self, now: float):
        pattern = LED_PATTERNS.get(self.state.get_level())
        if pattern is not None:
            return pattern
        if self.state.link_is_down(now):
            return LED_LINK_DOWN
        return None

    def tick(self, now: float):
        pattern = self._current_pattern(now)
        if pattern is not self._pattern:
            # restart from a known dark state so patterns never blend
            self._pattern = pattern
            self._set(False, LED_OFF)
            self._next_ts = now
        if pattern is None or now < self._next_ts:
            return
        colour, lit_s, dark_s = pattern
        self._set(not self._lit, colour)
        self._next_ts = now + (lit_s if self._lit else dark_s)

    def _set(self, lit: bool, colour):
        self._lit = lit
        self.leds.fill(colour if lit else LED_OFF)
        self.leds.show()


class Buzzer:
    """Beeps on its own clock, independent of the LEDs."""

    def __init__(self, state: AlertState):
        self.state = state
        self.sample = self._generate_sample()
        self.i2s = audiobusio.I2SOut(board.I2S_BCLK, board.I2S_WCLK, board.I2S_DOUT)
        self._pattern = None
        self._sounding = False
        self._next_ts = 0.0

    @staticmethod
    def _generate_sample():
        length = 8000 // 440
        audio_volume = 0.2
        sine_wave = array.array("H", [0] * length)
        for i in range(length):
            sine_wave[i] = int(audio_volume * math.sin(math.pi * 2 * i / length) * (2 ** 15) + 2 ** 15)
        return audiocore.RawSample(sine_wave, sample_rate=8000)

    def tick(self, now: float):
        pattern = BUZZER_PATTERNS.get(self.state.get_level())
        if pattern is not self._pattern:
            self._pattern = pattern
            self._set(False)
            self._next_ts = now
        if pattern is None or now < self._next_ts:
            return
        on_s, off_s = pattern
        self._set(not self._sounding)
        self._next_ts = now + (on_s if self._sounding else off_s)

    def _set(self, sounding: bool):
        self._sounding = sounding
        if sounding:
            self.i2s.play(self.sample, loop=True)
        elif self.i2s.playing:
            self.i2s.stop()


class ScrollBuffer:
    LINES = 12  # we can show 12 lines of text via stdout

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
        self.cursor = 0  # the offset we keep versus the last message
        self.messages = list()
        self.dirty = True

    def append(self, line: str):
        """Cheap enough to call from an MQTT callback - it does no output."""
        self.messages.append(line)
        if len(self.messages) > MAX_MESSAGES:
            del self.messages[0]
        elif self.cursor > 0:
            # keep whatever the user scrolled back to anchored, instead of
            # yanking the view out from under them
            self.cursor += 1
        self._clamp_cursor()
        self.dirty = True

    def poll_encoder(self, now: float):
        # rotaryio counts in hardware, so nothing is lost between polls
        position = self.encoder.position
        if position == self.last_position:
            return
        self.cursor += position - self.last_position
        self.last_position = position
        self._clamp_cursor()
        self.dirty = True

    def _clamp_cursor(self):
        self.cursor = max(0, min(self.cursor, len(self.messages) - self.LINES))

    def render_if_dirty(self, now: float):
        """Coalesced redraw: a burst of ten messages costs one repaint."""
        if not self.dirty:
            return
        self.dirty = False
        start_index = len(self.messages) - self.LINES - self.cursor
        for i in range(start_index, start_index + self.LINES):
            if 0 <= i < len(self.messages):
                print(self.messages[i])
            else:
                print("")

    def init_demo(self):
        for i in range(1, 21):
            self.messages.append(f"W 00:00:00 Line {i}")
        self.dirty = True


class DisplayManager:
    def __init__(self, state: AlertState):
        self.state = state
        self.keys = keypad.Keys((board.IO0,), value_when_pressed=True, pull=True)
        self.display = board.DISPLAY
        self._wake_requested = False

    def request_wake(self):
        """Cheap enough to call from an MQTT callback - it does no I/O."""
        self._wake_requested = True

    def poll_keys(self, now: float):
        # keypad queues events from a background handler, so a press is never
        # lost while we are elsewhere - only answered late
        pressed = False
        event = self.keys.events.get()
        while event:
            if event.pressed:
                pressed = True
            event = self.keys.events.get()

        if pressed:
            # a press always acknowledges, and toggles the screen
            if self.is_display_off():
                self.turn_display_on()
            else:
                self.display.brightness = 0
            self.state.clear()
            self._wake_requested = False
        elif self._wake_requested:
            self._wake_requested = False
            self.turn_display_on()

    def is_display_off(self) -> bool:
        return self.display.brightness == 0.0

    def turn_display_on(self):
        self.display.brightness = 1


class MqttLink:
    """Owns the broker connection. Never blocks longer than SOCKET_TIMEOUT, and
    never lets an exception escape into the main loop."""

    def __init__(self, state: AlertState, scroll_buffer: ScrollBuffer, display_manager: DisplayManager):
        self.state = state
        self.scroll_buffer = scroll_buffer
        self.display_manager = display_manager
        self.connected = False
        self._backoff = RECONNECT_BACKOFF_MIN
        self._next_attempt = 0.0

        pool = socketpool.SocketPool(wifi.radio)
        ssl_context = ssl.create_default_context()
        self.client = MQTT.MQTT(
            broker=os.getenv("MQTT_HOST"),
            port=int(os.getenv("MQTT_PORT")),
            username=os.getenv("MQTT_USER"),
            password=os.getenv("MQTT_PASS"),
            socket_pool=pool,
            ssl_context=ssl_context,
            socket_timeout=SOCKET_TIMEOUT,
            recv_timeout=RECV_TIMEOUT,
            keep_alive=MQTT_KEEP_ALIVE,
            # one attempt per call: we run the back-off ourselves so that
            # minimqtt never sleeps inside our loop
            connect_retries=1,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        self.state.link_down(time.monotonic())

    # --- scheduler entry points ---------------------------------------

    def poll(self, now: float):
        """Called every tick; also emits the keep-alive PINGREQ."""
        if not self.connected:
            return
        try:
            self.client.loop(timeout=SOCKET_TIMEOUT)
        except Exception as err:  # noqa - the pager must outlive the network
            print(f"mqtt poll failed: {err}")
            self._mark_down(now)

    def supervise(self, now: float):
        if self.connected or now < self._next_attempt:
            return
        self._next_attempt = now + self._backoff
        self._backoff = min(self._backoff * 2, RECONNECT_BACKOFF_MAX)
        try:
            print("Connecting to broker...")
            self.client.connect()
            self.connected = True
            self._backoff = RECONNECT_BACKOFF_MIN
            self.state.link_up()
        except Exception as err:  # noqa
            print(f"broker connect failed: {err}")

    def heartbeat(self, now: float):
        if not self.connected:
            return
        try:
            self.client.publish(heartbeat_topic, f"{now}")
        except Exception as err:  # noqa
            print(f"heartbeat failed: {err}")
            self._mark_down(now)

    def _mark_down(self, now: float):
        self.connected = False
        self.state.link_down(now)
        self._next_attempt = now + self._backoff

    # --- minimqtt callbacks -------------------------------------------
    # These run inside client.loop(), so they only touch state and set flags.

    def _on_connect(self, client, userdata, flags, rc):
        print(f"Connected to broker! Listening for topic changes on {alert_topic}")
        client.subscribe(alert_topic)

    def _on_disconnect(self, client, userdata, rc):
        # reconnecting from here would re-enter the client from inside its own
        # loop, so just flag it and let supervise() handle it
        print("Disconnected from broker.")
        self.connected = False
        self.state.link_down(time.monotonic())

    def _on_message(self, client, topic: str, message):
        if not message:
            self.state.clear()
            return
        if topic.endswith("warn"):
            line = f"W: {message}"
            self.state.raise_level(LEVEL_WARN)
        elif topic.endswith("error"):
            line = f"E: {message}"
            self.state.raise_level(LEVEL_ERROR)
        elif topic.endswith("critical"):
            line = f"!: {message}"
            self.state.raise_level(LEVEL_CRITICAL)
        else:
            line = f"I: {message}"

        self.scroll_buffer.append(line)
        self.display_manager.request_wake()


def connect_wifi():
    ssid = os.getenv("CIRCUITPY_WIFI_SSID")
    while True:
        try:
            print(f"Connecting to {ssid}")
            wifi.radio.connect(ssid, os.getenv("CIRCUITPY_WIFI_PASSWORD"))
            print(f"Connected to {ssid}!")
            return
        except Exception as err:  # noqa - a pager that fails to boot is dead
            print(f"WiFi connect failed: {err}")
            time.sleep(RECONNECT_BACKOFF_MIN)


# ----------------------------------------------------------------------- main

connect_wifi()

root = os.getenv("MQTT_TOPIC_ROOT")
alert_topic = f"{root}/alerts/+"
heartbeat_topic = f"{root}/heartbeat"

alertState = AlertState()
scrollBuffer = ScrollBuffer()
ledBlinker = LedBlinker(alertState)
buzzer = Buzzer(alertState)
displayManager = DisplayManager(alertState)
mqttLink = MqttLink(alertState, scrollBuffer, displayManager)

displayManager.turn_display_on()
# scrollBuffer.init_demo()

tasks = (
    Task(displayManager.poll_keys, 0.02),
    Task(scrollBuffer.poll_encoder, 0.02),
    Task(ledBlinker.tick, 0.05),
    Task(buzzer.tick, 0.05),
    Task(scrollBuffer.render_if_dirty, 0.10),
    Task(mqttLink.supervise, 1.0),
    Task(mqttLink.heartbeat, HEARTBEAT_PERIOD),
)

while True:
    # The MQTT poll paces the loop: it blocks for at most SOCKET_TIMEOUT and
    # yields to the background WiFi stack while it does. With no broker there
    # is nothing to block on, so sleep the same amount rather than spinning.
    mqttLink.poll(time.monotonic())
    if not mqttLink.connected:
        time.sleep(SOCKET_TIMEOUT)

    now = time.monotonic()
    for task in tasks:
        try:
            task.maybe_run(now)
        except Exception as err:  # noqa - one bad task must not kill the pager
            print(f"task error: {err}")
