#!/usr/bin/env python3
"""Construit une image de l'EEPROM émulée du firmware IER.

Le firmware range ses paramètres dans les onze dernières pages de la flash
(`../ier/src/sov_eeflash.c`) : dix pages tournantes pour les réglages de
régulation, une onzième pour les informations d'usine. Une flash effacée lit
0xFF partout ; c'est ce que voit une carte neuve, et le firmware s'en sert pour
décider qu'aucune page n'est valide — l'en-tête d'une page valide vaut 0x0000.

Trois images ont un sens :

    --blank    carte neuve, les onze pages à 0xFF. Le firmware part sur ses
               valeurs par défaut et se met en FACTORY_STATE, faute
               d'informations d'usine.
    --factory  idem, plus la page d'usine renseignée : le firmware quitte
               FACTORY_STATE au démarrage suivant.
    --patch    n'écrit que la page d'usine dans une image existante, en
               laissant intactes les pages déjà écrites par le firmware.

Les offsets ne sont jamais recopiés ici : ils sont relus dans
`../ier/include/sov_eeflash.h` à chaque exécution, de la même façon que
`console/symbols.py` relit les adresses dans le DWARF de l'ELF. Un firmware
dont le plan mémoire change reste donc lisible sans rien modifier — ces enums
n'apparaissent pas dans le DWARF (aucune variable n'est de ce type), l'en-tête
est la seule source disponible.

    python3 tools/mkeeprom.py --blank var/eeprom.bin
    python3 tools/mkeeprom.py --factory var/eeprom.bin
    python3 tools/mkeeprom.py --patch var/eeprom.bin
"""

import argparse
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = ROOT.parent / "ier/include/sov_eeflash.h"

# Informations d'usine par défaut. Seul le vecteur NO/NC a un effet sur la
# régulation (task_control.c : il choisit entre contact normalement ouvert et
# normalement fermé pour ENABLE, débit d'air et limite haute) ; à zéro, tous
# les contacts sont normalement fermés, ce que décrit le README. Le reste est
# rapporté à l'écran mais n'entre dans aucun calcul.
FACTORY_DEFAULTS = {
    "FSISERIALNUMBERLOW_E": 1001,
    "FSISERIALNUMBERHI_E": 0,
    "FSIMODEL_E": 5,            # IER17
    "FSIVOLTAGE_E": 7,          # VAC600
    "FSIPHASE_E": 3,            # PHASE3
    "FSIWLCALIB_E": 0,
    "FSIWTCALIB_E": 0,
    "FSIRHCALIB_E": 0,
    "FSIHLCALIB_E": 0,
    "FSIADCALIB_E": 0,
    "FSIETCALIB_E": 0,
    "FSIOUTCALIB_E": 0,
    "FSINONCVECTOR_E": 0,       # toutes les entrées normalement fermées
}


class LayoutError(RuntimeError):
    """L'en-tête du firmware n'a pas livré ce qui est attendu."""


class Layout:
    """Plan mémoire de l'EEPROM, relu dans sov_eeflash.h."""

    def __init__(self, header: Path = HEADER):
        self.header = Path(header)
        if not self.header.is_file():
            raise LayoutError(f"en-tête introuvable : {self.header}")
        text = self.header.read_text(encoding="utf-8", errors="replace")

        self.page_size = self._define(text, "FLASH_PAGE_SIZE")
        self.base_address = self._define(text, "FLASH_USER_START_ADDR")
        self.valid_page = self._define(text, "VALID_PAGE")
        self.erased_page = self._define(text, "ERASED_PAGE")

        pages = self._enum(text, "eeflash_page_e")
        # Les pages de données sont numérotées ; la page d'usine suit la
        # dernière d'entre elles (PAGE10 dans l'en-tête).
        self.data_pages = pages["NUMBER_OF_EEFPAGE"]
        self.factory_page = self.data_pages
        self.page_count = self.data_pages + 1

        self.items = self._enum(text, "eeflash_item_offset_e")
        self.factory_items = self._enum(text, "flash_system_info_e")

    @property
    def size(self) -> int:
        return self.page_count * self.page_size

    @staticmethod
    def _define(text: str, name: str) -> int:
        """Valeur d'un #define, écrite « ((uint32_t)0x800) /* commentaire */ »."""
        match = re.search(rf"^[ \t]*#define\s+{name}\s+(.+)$", text, re.M)
        if not match:
            raise LayoutError(f"{name} absent de sov_eeflash.h")
        body = re.sub(r"/\*.*?\*/|//.*", "", match.group(1))
        # Le transtypage précède la valeur : « ((uint16_t)0x0000) ».
        literal = re.findall(r"0[xX][0-9A-Fa-f]+|\b\d+\b",
                             re.sub(r"\b(?:u?int\d+_t|unsigned|signed)\b", "", body))
        if not literal:
            raise LayoutError(f"{name} sans valeur numérique dans sov_eeflash.h")
        return int(literal[-1], 0)

    @staticmethod
    def _enum(text: str, name: str) -> dict:
        """Membres d'un enum désigné par son typedef, avec leurs valeurs."""
        # [^{}] borne la capture à un seul bloc : sans cela, la recherche part
        # du premier « typedef enum » du fichier et avale les enums voisins.
        match = re.search(rf"typedef\s+enum\s*\{{([^{{}}]*)\}}\s*{name}\s*;",
                          text, re.S)
        if not match:
            raise LayoutError(f"enum {name} absent de sov_eeflash.h")
        members, previous = {}, -1
        for line in match.group(1).split("\n"):
            line = re.sub(r"/\*.*?\*/|//.*", "", line).strip()
            entry = re.match(r"^(\w+)\s*(?:=\s*([^,]+?))?\s*,?$", line)
            if not entry:
                continue
            raw = entry.group(2)
            if raw is None:
                value = previous + 1
            else:
                # Le transtypage porte des chiffres : « ((uint16_t)0x0009) ».
                raw = re.sub(r"\b(?:u?int\d+_t|unsigned|signed)\b", "", raw)
                digits = re.search(r"0[xX][0-9A-Fa-f]+|\b\d+\b", raw)
                if not digits:
                    continue
                value = int(digits.group(0), 0)
            members[entry.group(1)] = value
            previous = value
        if not members:
            raise LayoutError(f"enum {name} vide dans sov_eeflash.h")
        return members


def blank_image(layout: Layout) -> bytearray:
    """Les onze pages telles qu'une flash effacée les présente."""
    return bytearray(b"\xFF" * layout.size)


def write_factory_page(image: bytearray, layout: Layout, values: dict) -> None:
    """Renseigne la page d'usine ; les autres pages ne sont pas touchées."""
    start = layout.factory_page * layout.page_size
    image[start:start + layout.page_size] = b"\xFF" * layout.page_size

    def put(offset: int, value: int) -> None:
        image[start + offset:start + offset + 2] = struct.pack("<H", value & 0xFFFF)

    put(0, layout.valid_page)          # en-tête : page valide
    for name, value in values.items():
        if name not in layout.factory_items:
            raise LayoutError(f"{name} absent de flash_system_info_e")
        put(layout.factory_items[name], value)


def factory_image(layout: Layout, values: dict | None = None) -> bytearray:
    image = blank_image(layout)
    write_factory_page(image, layout, values or FACTORY_DEFAULTS)
    return image


def patch_factory(path: Path, layout: Layout,
                  values: dict | None = None) -> bytearray:
    """Ajoute la page d'usine à une image existante, sans rien perdre.

    Le firmware écrit ses propres pages dès les premières secondes : une image
    régénérée de zéro effacerait des réglages que la cible a déjà enregistrés.
    """
    if not path.is_file():
        return factory_image(layout, values)
    image = bytearray(path.read_bytes())
    if len(image) < layout.size:
        image.extend(b"\xFF" * (layout.size - len(image)))
    write_factory_page(image, layout, values or FACTORY_DEFAULTS)
    return image[:layout.size]


def _arguments(argv):
    parser = argparse.ArgumentParser(
        prog="mkeeprom",
        description="Construit une image de l'EEPROM émulée du firmware IER.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--blank", action="store_true",
                      help="carte neuve : onze pages à 0xFF (défaut)")
    mode.add_argument("--factory", action="store_true",
                      help="carte configurée en usine")
    mode.add_argument("--patch", action="store_true",
                      help="ajouter la page d'usine à une image existante")
    parser.add_argument("output", nargs="?", type=Path,
                        default=ROOT / "var/eeprom.bin",
                        help="image à écrire (défaut : var/eeprom.bin)")
    parser.add_argument("--header", type=Path, default=HEADER,
                        help="en-tête du firmware où lire le plan mémoire")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    options = _arguments(argv)
    try:
        layout = Layout(options.header)
        if options.patch:
            image = patch_factory(options.output, layout)
            mode = "usine ajoutée à l'image existante"
        elif options.factory:
            image = factory_image(layout)
            mode = "configurée en usine"
        else:
            image = blank_image(layout)
            mode = "carte neuve"
    except LayoutError as error:
        print(f"mkeeprom : {error}", file=sys.stderr)
        return 1

    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_bytes(bytes(image))
    print(f"{options.output} — {len(image)} octets, "
          f"{layout.page_count} pages de {layout.page_size} — {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
