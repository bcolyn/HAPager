# SPDX-FileCopyrightText: 2022 Dan Halbert for Adafruit Industries
#
# SPDX-License-Identifier: MIT

import asyncio
import board

import adafruit_dotstar as dotstar


async def blink(interval, count):  # Don't forget the async!
    with dotstar.DotStar(board.IO45, board.IO42, 7, brightness=0.2) as leds:
        leds.fill((0, 255, 0))
        for _ in range(count):
            print("ON")
            leds.brightness = 1
            await asyncio.sleep(interval)  # Don't forget the await!
            print("OFF")
            leds.brightness = 0
            await asyncio.sleep(interval)  # Don't forget the await!


async def main():  # Don't forget the async!
    led_task = asyncio.create_task(blink(2, 10))
    await asyncio.gather(led_task)  # Don't forget the await!
    print("done")


asyncio.run(main())
