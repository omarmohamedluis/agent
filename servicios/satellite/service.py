#!/usr/bin/env python3
import time, sys

NAME = "satellite"

def main():
    try:
        while True:
            print("[service:satellite] vivo cada 5s")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print("[service:satellite] adiós")

if __name__ == "__main__":
    main()
