import array
import math
import time

import audiobusio
import audiocore
import board

print("Hello World!")

display = board.DISPLAY

# Generate one period of sine wave.
length = 8000 // 440
vol = 0.1
sine_wave = array.array("H", [0] * length)
for i in range(length):
    sine_wave[i] = int(vol * math.sin(math.pi * 2 * i / length) * (2 ** 15) + 2 ** 15)

sine_wave = audiocore.RawSample(sine_wave, sample_rate=8000)
i2s = audiobusio.I2SOut(board.I2S_BCLK, board.I2S_WCLK, board.I2S_DOUT)

while True:
    display.brightness = 0.0
    i2s.play(sine_wave, loop=True)
    time.sleep(1)      # Wait for 1 second

    display.brightness = 1.0
    i2s.stop()
    time.sleep(1)      # Wait for 1 second