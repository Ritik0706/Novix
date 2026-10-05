# synola/management/commands/suggest_model.py
from django.core.management.base import BaseCommand
from synola.services.hardware import get_hardware_profile
from synola.services.model_recommender import recommend_model, install_recommended_model

class Command(BaseCommand):
    help = "Detect hardware, suggest a fitting model, and optionally install it."

    def add_arguments(self, parser):
        parser.add_argument("--install", action="store_true")

    def handle(self, *args, **opts):
        profile = get_hardware_profile()
        suggestion = recommend_model(profile)
        self.stdout.write(f"Detected: {profile}")
        self.stdout.write(f"Suggested: {suggestion['model']['model']} — {suggestion['reason']}")
        if opts["install"]:
            install_recommended_model(suggestion)
            self.stdout.write("Installed and swapped.")