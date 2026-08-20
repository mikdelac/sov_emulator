"""Résolution des adresses des variables du firmware depuis le DWARF de l'ELF.

La console lit l'état interne du firmware (`ier_core.ier_state`,
`ier_core.alarm1`...) à des adresses absolues sur le bus. Les figer dans le
code casserait au premier champ inséré dans `ier_core_t` — et silencieusement,
puisqu'une adresse fausse retourne quand même une valeur. Elles sont donc
extraites à chaque démarrage de l'ELF que Renode charge.

    >>> table = SymbolTable("../ier/Debug/ier.elf")
    >>> table.resolve("ier_core.ier_state")
    Symbol(expr='ier_core.ier_state', address=0x20000258, size=4)
"""

from dataclasses import dataclass
from pathlib import Path

from elftools.elf.elffile import ELFFile

# Qualificatifs traversés sans rien changer à l'adresse ni à la taille.
_TRANSPARENT_TAGS = {
    "DW_TAG_typedef",
    "DW_TAG_const_type",
    "DW_TAG_volatile_type",
    "DW_TAG_restrict_type",
}

_DW_OP_ADDR = 0x03
_DW_OP_PLUS_UCONST = 0x23


class SymbolError(LookupError):
    """Symbole ou champ introuvable dans le DWARF de l'ELF."""


@dataclass(frozen=True)
class Symbol:
    expr: str
    address: int
    size: int

    @property
    def width(self) -> int:
        """Largeur d'accès utilisable par les commandes sysbus Read*."""
        return self.size if self.size in (1, 2, 4, 8) else 4

    def __repr__(self) -> str:
        return (f"Symbol(expr={self.expr!r}, address=0x{self.address:08X}, "
                f"size={self.size})")


@dataclass(frozen=True)
class Indirect:
    """Champ atteint par un pointeur, p. ex. « ier_core.database->wt ».

    L'adresse finale n'existe qu'à l'exécution : `ier_core.param` et
    `ier_core.database` sont alloués par pvPortMalloc au démarrage du
    firmware. On mémorise donc où lire le pointeur, puis de combien se
    décaler dans la structure pointée.
    """

    expr: str
    pointer: Symbol
    offset: int
    size: int
    kind: str = "int"       # « float » ou « int »

    def target(self, pointer_value: int) -> int:
        return pointer_value + self.offset


def _address_from_location(value) -> int | None:
    """Adresse absolue d'un DW_AT_location de la forme « DW_OP_addr <n> »."""
    if not isinstance(value, (list, bytes, bytearray)) or len(value) < 5:
        return None
    if value[0] != _DW_OP_ADDR:
        return None                      # variable locale, registre, TLS...
    return int.from_bytes(bytes(value[1:5]), "little")


def _member_offset(die) -> int:
    """Décalage d'un DW_TAG_member, en constante ou en expression DWARF."""
    attr = die.attributes.get("DW_AT_data_member_location")
    if attr is None:
        return 0                         # membre d'union
    value = attr.value
    if isinstance(value, int):
        return value
    if isinstance(value, (list, bytes, bytearray)) and value:
        if value[0] == _DW_OP_PLUS_UCONST:
            offset, shift = 0, 0
            for byte in bytes(value[1:]):
                offset |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            return offset
    raise SymbolError(f"décalage de membre non interprétable : {value!r}")


class SymbolTable:
    """Adresses des variables globales du firmware, lues dans son DWARF."""

    def __init__(self, elf_path):
        self.path = Path(elf_path)
        if not self.path.is_file():
            raise SymbolError(f"ELF introuvable : {self.path}")
        self._handle = self.path.open("rb")
        elf = ELFFile(self._handle)
        if not elf.has_dwarf_info():
            raise SymbolError(
                f"{self.path} ne contient pas de DWARF — compilez la cible "
                f"Debug (make -C ../ier/Debug)")
        self._dwarf = elf.get_dwarf_info()
        self._globals = self._index_globals()
        self._cache: dict[str, Symbol] = {}

    def close(self) -> None:
        self._handle.close()

    def _index_globals(self) -> dict:
        """Variables ayant une adresse fixe, indexées par nom.

        Une même variable apparaît dans plusieurs unités de compilation : une
        définition porteuse de DW_AT_location et des déclarations qui n'en ont
        pas. Seule la définition nous intéresse.
        """
        found = {}
        for unit in self._dwarf.iter_CUs():
            for die in unit.iter_DIEs():
                if die.tag != "DW_TAG_variable":
                    continue
                name = die.attributes.get("DW_AT_name")
                location = die.attributes.get("DW_AT_location")
                if name is None or location is None:
                    continue
                address = _address_from_location(location.value)
                if address is None:
                    continue
                found.setdefault(name.value.decode(), (address, die))
        return found

    @staticmethod
    def _strip(die):
        """Traverse typedef / const / volatile jusqu'au type porteur."""
        while die is not None and die.tag in _TRANSPARENT_TAGS:
            if "DW_AT_type" not in die.attributes:
                return None
            die = die.get_DIE_from_attribute("DW_AT_type")
        return die

    def _walk_members(self, type_die, fields, expr: str):
        """Descend une suite de champs et renvoie (décalage, type atteint)."""
        offset = 0
        for field_name in fields:
            if type_die is None or type_die.tag not in (
                    "DW_TAG_structure_type", "DW_TAG_union_type"):
                raise SymbolError(
                    f"{expr} : « {field_name} » demandé sur un type qui n'est "
                    f"pas une structure")
            for child in type_die.iter_children():
                if child.tag != "DW_TAG_member":
                    continue
                name = child.attributes.get("DW_AT_name")
                if name is not None and name.value.decode() == field_name:
                    offset += _member_offset(child)
                    type_die = self._strip(
                        child.get_DIE_from_attribute("DW_AT_type"))
                    break
            else:
                raise SymbolError(f"{expr} : champ « {field_name} » absent")
        return offset, type_die

    def _resolve_path(self, expr: str):
        """Adresse absolue et type atteint pour un chemin sans pointeur."""
        root, *fields = expr.split(".")
        entry = self._globals.get(root)
        if entry is None:
            raise SymbolError(f"variable globale introuvable : {root}")
        address, die = entry
        type_die = self._strip(die.get_DIE_from_attribute("DW_AT_type"))
        offset, type_die = self._walk_members(type_die, fields, expr)
        return address + offset, type_die

    @staticmethod
    def _size_of(type_die, default: int = 4) -> int:
        attribute = (type_die.attributes.get("DW_AT_byte_size")
                     if type_die is not None else None)
        return attribute.value if attribute is not None else default

    @staticmethod
    def _kind_of(type_die) -> str:
        """« float » si le type de base est un flottant IEEE754, sinon « int »."""
        attribute = (type_die.attributes.get("DW_AT_encoding")
                     if type_die is not None else None)
        return "float" if attribute is not None and attribute.value == 4 else "int"

    def resolve(self, expr: str) -> Symbol:
        """Adresse et taille de `expr`, p. ex. « ier_core.alarm1 »."""
        cached = self._cache.get(expr)
        if cached is not None:
            return cached
        address, type_die = self._resolve_path(expr)
        symbol = Symbol(expr, address, self._size_of(type_die))
        self._cache[expr] = symbol
        return symbol

    def resolve_indirect(self, expr: str) -> Indirect:
        """Résout un chemin à un saut de pointeur, « base->champ.sous_champ »."""
        if "->" not in expr:
            raise SymbolError(f"{expr} : pas de « -> », utilisez resolve()")
        left, right = expr.split("->", 1)
        address, type_die = self._resolve_path(left)
        if type_die is None or type_die.tag != "DW_TAG_pointer_type":
            raise SymbolError(f"{expr} : « {left} » n'est pas un pointeur")
        pointer = Symbol(left, address, self._size_of(type_die))
        target_die = self._strip(type_die.get_DIE_from_attribute("DW_AT_type"))
        offset, field_die = self._walk_members(
            target_die, right.split("."), expr)
        return Indirect(expr, pointer, offset, self._size_of(field_die),
                        self._kind_of(field_die))

    def resolve_all(self, exprs) -> dict[str, Symbol]:
        """Résout une liste d'expressions, en ignorant celles qui échouent.

        Un firmware plus ancien peut ne pas avoir tous les champs ; mieux vaut
        une console amputée d'une valeur qu'une console qui refuse de démarrer.
        """
        resolved = {}
        for expr in exprs:
            try:
                resolved[expr] = (self.resolve_indirect(expr) if "->" in expr
                                  else self.resolve(expr))
            except SymbolError:
                continue
        return resolved
