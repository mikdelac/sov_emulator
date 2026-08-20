"""Point d'entrée de la console : « python -m console » ou « ./console.sh »."""

import argparse
import sys
from pathlib import Path

from .session import Session

ROOT = Path(__file__).resolve().parent.parent


def _arguments(argv):
    parser = argparse.ArgumentParser(
        prog="console",
        description="Console d'entrées / sorties du firmware IER émulé dans "
                    "Renode. Pilote les capteurs et les entrées tout ou rien, "
                    "et observe l'état réel du firmware — machine à états, "
                    "alarmes, sorties — lu dans sa mémoire et ses registres.")
    parser.add_argument(
        "--elf", type=Path, default=ROOT.parent / "ier/Debug/ier.elf",
        help="firmware à charger ; doit contenir le DWARF (cible Debug)")
    parser.add_argument(
        "--script", type=Path, default=ROOT / "scripts/ier.resc",
        help="script Renode d'initialisation de la plateforme")
    parser.add_argument(
        "--attach", type=int, metavar="PORT",
        help="rejoindre une instance Renode déjà lancée avec « -P PORT » "
             "au lieu d'en démarrer une")
    parser.add_argument(
        "--interval", type=int, default=150, metavar="MS",
        help="période de sondage en millisecondes (défaut : 150)")
    parser.add_argument(
        "--self-check", action="store_true",
        help="vérifier la cohérence avec scripts/sensors.resc et l'ELF, "
             "puis sortir sans ouvrir de fenêtre")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    options = _arguments(argv)
    session = Session(ROOT, elf=options.elf, script=options.script,
                      attach_port=options.attach)

    if options.self_check:
        from . import selfcheck
        return selfcheck.run(session)

    if not options.elf.is_file():
        print(f"Firmware introuvable : {options.elf}\n"
              f"Compilez-le d'abord :  make -C ../ier/Debug", file=sys.stderr)
        return 1

    from PyQt6.QtWidgets import QApplication
    from .window import ConsoleWindow

    application = QApplication(sys.argv[:1])
    application.setApplicationName("Console IER")
    window = ConsoleWindow(session, interval_ms=options.interval)
    window.show()
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
