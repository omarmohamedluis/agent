#!/usr/bin/env python3
import time, sys

NAME = "MIDI"

def main():
    try:
        while True:
            print("[service:MIDI] vivo cada 5s")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print("[service:MIDI] adiós")

if __name__ == "__main__":
    main()
