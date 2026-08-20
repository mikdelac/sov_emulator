"""Vérification de cohérence entre la console et les scripts Renode.

Les macros affichées dans la console sont exécutées par Renode
(`runMacro $nom`) mais la console applique en parallèle un correctif local pour
repositionner ses curseurs. Si les deux divergent, l'interface ment sans que
rien ne le signale : les curseurs indiquent une valeur, la cible en a une
autre. Ce contrôle relit `scripts/sensors.resc` et compare, macro par macro,
les commandes que Renode exécutera à celles que la console déduit.

    python -m console --self-check
"""

import re
from pathlib import Path

from . import firmware as fw
from .session import Session

_MACRO = re.compile(r'^macro\s+(\w+)\s*$\s*"""(.*?)"""', re.M | re.S)
_SET_VOLTAGE = re.compile(r"sysbus\.adc1\s+SetVoltage\s+(\d+)\s+([\d.]+)")
_ON_GPIO = re.compile(r"sysbus\.gpioPort(\w+)\s+OnGPIO\s+(\d+)\s+(true|false)")

_TOLERANCE = 1e-3
# Canaux réglés une fois pour toutes au reset et jamais exposés dans l'IHM.
_UNEXPOSED = set(fw.FIXED_CHANNELS)


def _parse(script: Path) -> dict:
    """Commandes de chaque macro du script, sous une forme comparable."""
    macros = {}
    for name, body in _MACRO.findall(script.read_text(encoding="utf-8")):
        voltages = {int(channel): float(volts)
                    for channel, volts in _SET_VOLTAGE.findall(body)
                    if int(channel) not in _UNEXPOSED}
        gpios = {(port, int(bit)): value == "true"
                 for port, bit, value in _ON_GPIO.findall(body)}
        macros[name] = (voltages, gpios)
    return macros


def _expected(macro) -> tuple[dict, dict]:
    """Ce que la console déduit du correctif local de la macro."""
    voltages, gpios = {}, {}
    for key, value in macro.patch.items():
        if key in fw.ANALOG_BY_KEY:
            analog = fw.ANALOG_BY_KEY[key]
            voltages[analog.channel] = analog.to_volts(float(value))
        elif key in fw.DIGITAL_BY_KEY:
            digital = fw.DIGITAL_BY_KEY[key]
            gpios[(digital.port, digital.bit)] = bool(value)
    return voltages, gpios


def check_macros(script: Path) -> list:
    """Écarts entre `scripts/sensors.resc` et `firmware.MACROS`."""
    if not script.is_file():
        return [f"{script} introuvable"]
    actual = _parse(script)
    problems = []
    for macro in fw.MACROS:
        if macro.name not in actual:
            problems.append(f"{macro.name} : absente de {script.name}")
            continue
        script_volts, script_gpios = actual[macro.name]
        console_volts, console_gpios = _expected(macro)
        for channel in sorted(set(script_volts) | set(console_volts)):
            left, right = script_volts.get(channel), console_volts.get(channel)
            if left is None or right is None or abs(left - right) > _TOLERANCE:
                problems.append(
                    f"{macro.name} : canal {channel} — sensors.resc {left}, "
                    f"console {right}")
        if script_gpios != console_gpios:
            problems.append(
                f"{macro.name} : entrées TOR — sensors.resc {script_gpios}, "
                f"console {console_gpios}")
    for name in sorted(set(actual) - {m.name for m in fw.MACROS}):
        if name not in ("factory_calibration", "reset"):
            problems.append(f"{name} : présente dans sensors.resc, absente de "
                            f"la console")
    return problems


def check_symbols(elf: Path) -> list:
    """Symboles attendus par la console mais absents de l'ELF."""
    from .symbols import SymbolError, SymbolTable
    try:
        table = SymbolTable(elf)
    except SymbolError as error:
        return [str(error)]
    resolved = table.resolve_all(fw.WATCHED_SYMBOLS + fw.WATCHED_INDIRECT)
    table.close()
    return [f"{expr} : introuvable dans {elf.name}"
            for expr in fw.WATCHED_SYMBOLS + fw.WATCHED_INDIRECT
            if expr not in resolved]


def run(session: Session) -> int:
    """Exécute les contrôles et rend un code de sortie de type shell."""
    problems = (check_macros(session.root / "scripts/sensors.resc")
                + check_symbols(session.elf))
    if not problems:
        print(f"macros cohérentes avec scripts/sensors.resc "
              f"({len(fw.MACROS)} vérifiées)")
        print(f"symboles résolus dans {session.elf} "
              f"({len(fw.WATCHED_SYMBOLS) + len(fw.WATCHED_INDIRECT)} attendus)")
        return 0
    for problem in problems:
        print(f"écart : {problem}")
    return 1
