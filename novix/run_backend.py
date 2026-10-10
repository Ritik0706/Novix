import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "novix.settings")

import logging
import django

from waitress import serve
from novix.wsgi import application

from django.core.management import call_command

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')

def main():
    logging.info("Initializing Novix backend...")

    django.setup()

    logging.info("Applying database migrations...")
    call_command("migrate", interactive=False)

    from synola.services.inference_engine import engine
    from synola.services.inference_engine import ACTIVE_PATH

    if ACTIVE_PATH.exists():
        logging.info("Starting inference engine...")
        engine.start()
    else:
        logging.info("No active model found; serving setup endpoints first.")

    try:
        logging.info("Database ready.")
        logging.info("Starting Novix backend on http://127.0.0.1:8000")
        serve(application, host="127.0.0.1", port=8000)
    finally:
        logging.info("Stopping inference engine...")
        engine.stop()


if __name__ == "__main__":
    main()