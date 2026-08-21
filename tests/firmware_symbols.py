"""Adresses des variables du firmware, pour les tests Robot.

Les champs observés par les tests (`ier_core.ier_state`, `ier_core.alarm2`…)
vivent à des offsets qui changent dès qu'on insère un membre dans `ier_core_t`.
Les écrire dans les fichiers `.robot` reviendrait à tester une adresse plutôt
qu'un comportement, et à échouer en silence — une adresse fausse renvoie une
valeur, pas une erreur.

Ce module ne fait que réutiliser `console/symbols.py`, qui lit le DWARF de
l'ELF chargé par Renode ; il n'y a donc qu'une seule source d'adresses dans le
dépôt, partagée par la console et les tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from console.symbols import SymbolTable      # noqa: E402

_tables: dict = {}


def _table(elf: str) -> SymbolTable:
    """Une table par ELF : ouvrir le DWARF coûte trop pour le refaire à chaque
    lecture, et les tests d'une suite portent tous sur le même binaire."""
    key = str(Path(elf).resolve())
    if key not in _tables:
        _tables[key] = SymbolTable(key)
    return _tables[key]


def symbol_address(elf: str, expression: str) -> int:
    """Adresse absolue d'une variable ou d'un champ, « ier_core.alarm2 »."""
    return _table(elf).resolve(expression).address


def symbol_width(elf: str, expression: str) -> int:
    """Taille en octets du même champ, pour masquer la lecture du bus."""
    return _table(elf).resolve(expression).width
