import rotaryio
import board
import digitalio

pin = digitalio.DigitalInOut(board.IO2)
with pin:
    pin.direction = digitalio.Direction.INPUT

pin = digitalio.DigitalInOut(board.IO1)
with pin:
    pin.direction = digitalio.Direction.INPUT

encoder = rotaryio.IncrementalEncoder(board.IO2, board.IO1,divisor=2)
last_position = None
while True:
    position = encoder.position
    if last_position is None or position != last_position:
        print(position)
    last_position = position
