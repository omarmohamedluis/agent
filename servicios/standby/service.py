#!/usr/bin/env python3
import time, sys

NAME = "standby"

def main():
    try:
        while True:
            print("[service:standby] vivo cada 5s")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print("[service:standby] adiós")

if __name__ == "__main__":
    main()
