#!/usr/bin/env python3
import time, sys

NAME = "scauting"

def main():
    try:
        while True:
            print("[service:scauting] vivo cada 5s")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print("[service:scauting] adiós")

if __name__ == "__main__":
    main()
