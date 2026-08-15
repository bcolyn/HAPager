#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["paho-mqtt>=2.0"]
# ///
"""Desktop test harness for the pager: publishes alerts to the broker it listens on.

This is a regular CPython script, not part of the firmware - it is never copied to
the device. It reads the same settings.toml the pager does, so there is only ever
one place holding the broker credentials.

    uv run pager_test.py send warn "Freezer door is open"
    uv run pager_test.py send critical "Water leak in the basement"
    uv run pager_test.py clear
    uv run pager_test.py demo
    uv run pager_test.py watch

The paho-mqtt dependency is declared inline above, so uv installs it on first run.
"""

from __future__ import annotations

import argparse
import string
import sys
import time
import tomllib
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt is not installed - run this script with: uv run pager_test.py ...")

# the pager derives the level from the last path segment; these are the ones it knows
LEVELS = ("info", "warn", "error", "critical")

REQUIRED_KEYS = ("MQTT_HOST", "MQTT_PORT", "MQTT_TOPIC_ROOT")

# ports we assume are plaintext; everything else is treated as TLS, matching the
# firmware which always hands minimqtt an ssl context
PLAIN_PORTS = (1883, 1884)


def find_settings() -> Path:
    """settings.toml lives next to this script, or on the mounted CIRCUITPY drive."""
    candidates = [Path(__file__).parent / "settings.toml"]
    for letter in string.ascii_uppercase[3:]:  # D: onwards
        drive = Path(f"{letter}:/")
        if (drive / "code.py").exists() and (drive / "settings.toml").exists():
            candidates.append(drive / "settings.toml")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    sys.exit(
        "No settings.toml found. Put a copy next to this script, plug in the device, "
        "or pass --settings <path>."
    )


def load_settings(path: Path | None) -> dict:
    path = path or find_settings()
    with path.open("rb") as handle:
        settings = tomllib.load(handle)
    missing = [key for key in REQUIRED_KEYS if not settings.get(key)]
    if missing:
        sys.exit(f"{path} is missing: {', '.join(missing)}")
    print(f"# settings: {path}")
    return settings


def connect(settings: dict, tls: bool | None) -> mqtt.Client:
    host = str(settings["MQTT_HOST"])
    # CircuitPython 10 hands os.getenv() results back as strings, so settings.toml
    # may well quote the port - accept it either way
    port = int(settings["MQTT_PORT"])
    if tls is None:
        tls = port not in PLAIN_PORTS

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if settings.get("MQTT_USER"):
        client.username_pw_set(str(settings["MQTT_USER"]), str(settings.get("MQTT_PASS", "")))
    if tls:
        client.tls_set()

    print(f"# connecting to {host}:{port} ({'tls' if tls else 'plain'})")
    client.connect(host, port, keepalive=60)
    client.loop_start()
    return client


def publish(client: mqtt.Client, topic: str, payload: str):
    info = client.publish(topic, payload, qos=1)
    info.wait_for_publish(timeout=10)
    shown = payload if payload else "<empty - clears the alert>"
    print(f"-> {topic}: {shown}")


def cmd_send(args, settings, client):
    root = settings["MQTT_TOPIC_ROOT"]
    publish(client, f"{root}/alerts/{args.level}", args.message)


def cmd_clear(args, settings, client):
    # an empty payload on any alert topic resets the level; the firmware does not
    # care which one, but info is the least surprising thing to see in a broker log
    root = settings["MQTT_TOPIC_ROOT"]
    publish(client, f"{root}/alerts/{args.level}", "")


def cmd_demo(args, settings, client):
    """Walk the levels so you can watch the LEDs and buzzer escalate, then clear."""
    root = settings["MQTT_TOPIC_ROOT"]
    script = [
        ("info", "Demo: info, screen only"),
        ("warn", "Demo: warn, yellow LEDs"),
        ("error", "Demo: error, red LEDs"),
        ("critical", "Demo: critical, red LEDs and beep"),
    ]
    for level, message in script:
        publish(client, f"{root}/alerts/{level}", message)
        time.sleep(args.delay)
    if args.clear:
        publish(client, f"{root}/alerts/info", "")


def cmd_flood(args, settings, client):
    """Fill the scroll buffer, to check the display coalesces a burst."""
    root = settings["MQTT_TOPIC_ROOT"]
    for i in range(1, args.count + 1):
        publish(client, f"{root}/alerts/{args.level}", f"{args.message} {i}/{args.count}")
        time.sleep(args.delay)


def cmd_watch(args, settings, client):
    """Tail everything under the topic root - mainly to see the pager's heartbeat."""
    root = settings["MQTT_TOPIC_ROOT"]
    last_heartbeat = {"ts": None}

    def on_message(_client, _userdata, msg):
        payload = msg.payload.decode("utf-8", "replace")
        stamp = time.strftime("%H:%M:%S")
        if msg.topic == f"{root}/heartbeat":
            gap = ""
            now = time.monotonic()
            if last_heartbeat["ts"] is not None:
                gap = f" (+{now - last_heartbeat['ts']:.1f}s)"
            last_heartbeat["ts"] = now
            print(f"{stamp} <3 uptime {payload}{gap}")
        else:
            print(f"{stamp} <- {msg.topic}: {payload or '<empty>'}")

    client.on_message = on_message
    client.subscribe(f"{root}/#", qos=1)
    print(f"# watching {root}/# - ctrl-c to stop")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--settings", type=Path, help="path to settings.toml")
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument("--tls", dest="tls", action="store_true", default=None,
                     help="force TLS (default: on unless the port is 1883)")
    tls.add_argument("--no-tls", dest="tls", action="store_false", help="force plaintext")

    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="publish one alert")
    send.add_argument("level", choices=LEVELS)
    send.add_argument("message", nargs="+")
    send.set_defaults(func=cmd_send)

    clear = sub.add_parser("clear", help="publish an empty payload to reset the alert level")
    clear.add_argument("level", nargs="?", default="info", choices=LEVELS)
    clear.set_defaults(func=cmd_clear)

    demo = sub.add_parser("demo", help="escalate through every level")
    demo.add_argument("--delay", type=float, default=5.0, help="seconds between levels")
    demo.add_argument("--no-clear", dest="clear", action="store_false",
                      help="leave the pager alerting at critical")
    demo.set_defaults(func=cmd_demo)

    flood = sub.add_parser("flood", help="send a burst of messages")
    flood.add_argument("count", type=int, nargs="?", default=20)
    flood.add_argument("--level", default="info", choices=LEVELS)
    flood.add_argument("--message", default="Flood")
    flood.add_argument("--delay", type=float, default=0.1)
    flood.set_defaults(func=cmd_flood)

    watch = sub.add_parser("watch", help="subscribe to the whole topic root")
    watch.set_defaults(func=cmd_watch)

    return parser


def main():
    args = build_parser().parse_args()
    if isinstance(getattr(args, "message", None), list):
        args.message = " ".join(args.message)

    settings = load_settings(args.settings)
    client = connect(settings, args.tls)
    try:
        args.func(args, settings, client)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
