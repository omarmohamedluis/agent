#!/usr/bin/env python3
import time, sys

NAME = "OSCnum"

def main():
    try:
        while True:
            print("[service:OSCnum] vivo cada 5s")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print("[service:OSCnum] adiós")

if __name__ == "__main__":
    main()
