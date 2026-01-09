#!/usr/bin/env python3
import time, sys

import logging
import os

# Configurar logging
log_path = os.environ.get("OMI_LOG_PATH")
handlers = []

if log_path:
    handlers.append(logging.FileHandler(log_path, encoding='utf-8'))
else:
    handlers.append(logging.FileHandler("service.log", encoding='utf-8'))
    handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=handlers
)
LOGGER = logging.getLogger("satellite")

NAME = "satellite"

def main():
    try:
        LOGGER.info("Servicio Satellite iniciado")
        while True:
            LOGGER.info("vivo cada 5s")
            time.sleep(5)
    except KeyboardInterrupt:
        LOGGER.info("adiós")

if __name__ == "__main__":
    main()
