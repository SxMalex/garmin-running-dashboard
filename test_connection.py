"""Test de connexion à Garmin Connect avec cache de session.

Pattern officiel python-garminconnect : `login(tokenstore)` gère tout —
resume depuis le cache si valide, refresh proactif si le token expire bientôt,
fallback sur login complet sinon, callback MFA si requis, et dump des tokens
sur disque après login réussi.
"""

import os
import sys
from datetime import date

from dotenv import load_dotenv
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

DEFAULT_TOKENSTORE = "~/.garminconnect"


def main() -> int:
    load_dotenv()

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    tokenstore = os.environ.get("GARMIN_TOKENSTORE", DEFAULT_TOKENSTORE)

    if not email or not password:
        print(
            "Erreur : renseigne GARMIN_EMAIL et GARMIN_PASSWORD dans .env",
            file=sys.stderr,
        )
        return 2

    print(f"→ Connexion à Garmin Connect (tokenstore: {tokenstore})")

    try:
        client = Garmin(
            email,
            password,
            prompt_mfa=lambda: input("Code MFA : ").strip(),
        )
        client.login(tokenstore)
    except GarminConnectAuthenticationError as e:
        print(f"Authentification refusée : {e}", file=sys.stderr)
        return 1
    except GarminConnectTooManyRequestsError as e:
        print(f"Rate-limit Garmin (429) : {e}", file=sys.stderr)
        print("  Attends quelques heures avant de retenter.", file=sys.stderr)
        return 1
    except GarminConnectConnectionError as e:
        print(f"Problème de connexion réseau : {e}", file=sys.stderr)
        return 1

    print("✓ Connexion active\n")

    print(f"  Nom complet  : {client.get_full_name()}")
    print(f"  Système      : {client.get_unit_system()}")

    today = date.today().isoformat()
    stats = client.get_stats(today)
    print(f"  Pas ({today}) : {stats.get('totalSteps')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
