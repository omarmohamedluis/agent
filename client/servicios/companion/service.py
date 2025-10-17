#!/usr/bin/env python3
import time, sys

NAME = "companion"

def main():
    try:
        while True:
            print("[service:companion] vivo cada 5s")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print("[service:companion] adiós")

if __name__ == "__main__":
    main()
