#!/usr/bin/env python3
import time, sys

NAME = "OSC"

def main():
    try:
        while True:
            print("[service:OSCkey] vivo cada 5s")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print("[service:OSCkey] adiós")

if __name__ == "__main__":
    main()
