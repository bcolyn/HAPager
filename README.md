# Home automation pager based on the LILYGO T-Embed

This configures a device to function as a pager, designed to alert users when a home automation system requires urgent attention.

It is built using the [LILYGO T-Embed](https://lilygo.cc/products/t-embed) package with 
[CircuitPython](https://circuitpython.org/board/lilygo_tembed_esp32s3/).

## Motivation

Push notifications to smartphones can have a pretty high latency (especially if the device is conserving battery) and
require internet access.

Notifications/messages on a web interface are only seen when the page is open and you're looking at it.

This system runs entirely locally with a local MQTT broker and responds almost immediately (<1s) to the first message.

This device will connect to the local WiFi and requires an MQTT broker. Anything that publishes a plain text message to
the relevant topic tree will trigger the pager, I have it listen to messages from HomeAssistant automations,
N.I.N.A. astro-imaging software and custom controller code (like an ESP32 hooked up to an RG9 optical rain sensor).

## Features:

* 4 levels of messages:
    * info: only turns on the screen
    * warn: turns on the screen and yellow blinking LEDs
    * error: turns on the screen and red blinking LEDs
    * critical: turns on the screen, red blinking LEDs and audible beeping
* scrollable buffer of messages on the rotary encoder
* screen on/off with the main button
* heartbeat message so the device itself can be monitored (and the home automation system can decide to send the alert
  elsewhere)

## Installation

* install CircuitPython on the T-Embed (I used the web installer, non-UF2)
* install dependencies from the [CircuitPython Libraries](https://circuitpython.org/libraries)
    * adafruit_minimqtt
    * adafruit_dotstar
* copy code.py to the root of the CIRCUITPY drive

## Configuration

* configure the pager in new file "settings.toml" on the CIRCUITPY drive:

    ```
    CIRCUITPY_WIFI_SSID = "<YOUR NETWORK SSID>"
    CIRCUITPY_WIFI_PASSWORD = "<YOUR NETWORK PASSWORD>"
    
    MQTT_HOST = "<YOUR BROKER IP>"
    MQTT_PORT = 1883
    MQTT_USER = "<YOUR BROKER USERNAME>"
    MQTT_PASS = "<YOUR BROKER PASSWORD>"
    MQTT_TOPIC_ROOT = "myplace/pager"
    ```

* configure the relevant piece of software to send its message to the right topic.
  In Home Assistant this can be an MQTT publish step in an automation. Assuming `MQTT_TOPIC_ROOT` is `myplace/pager`
  errors should be sent to `myplace/pager/alerts/error`, critical messages to `myplace/pager/alerts/critical` warnings
  to
  `myplace/pager/alerts/warn` and info messages to `myplace/pager/alerts/info`. The pager will send heartbeat messages
  to
  `myplace/pager/heartbeat`

## Usage:

* apply power via USB-C
* check that it connects to the WiFi and MQTT broker
* turn the screen off (message polling happens in the background) with the main button
* screen will light up when a message is received, acknowledge with the main button

References:

* https://lilygo.cc/products/t-embed
* https://circuitpython.org/board/lilygo_tembed_esp32s3/

