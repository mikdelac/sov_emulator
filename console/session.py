"""Pont entre la console et une machine IER émulée dans Renode.

Cette couche ne connaît rien de Qt : elle démarre (ou rejoint) une instance
Renode, résout les adresses du firmware, construit les commandes du Monitor et
transforme le résultat d'une rafale de lectures en un instantané exploitable.

Le principe directeur : **aucune valeur affichée n'est recalculée ici**. L'état
de la machine, les alarmes et les sorties viennent tous de la mémoire et des
registres du firmware qui tourne réellement. La console pilote les entrées et
observe ; elle ne simule pas la régulation en parallèle.
"""

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import firmware as fw
from .monitor import MonitorError, RenodeMonitor, RenodeProcess
from .symbols import SymbolError, SymbolTable

_VIRTUAL_TIME = re.compile(r"Elapsed Virtual Time:\s*(\d+):(\d+):([\d.]+)")

# Bits d'activation de sortie dans TIMx_CCER, un par canal.
_CCER_OFFSET = 0x20
_CCER_ENABLE_BIT = {1: 0, 2: 4, 3: 8, 4: 12}


def _as_float(raw: int) -> float:
    """Réinterprète un mot de 32 bits en flottant IEEE754 simple précision."""
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


def _load_mkeeprom(root: Path):
    """Charge tools/mkeeprom.py, qui n'est pas un module du paquet console.

    Le générateur d'image vit avec les outils du dépôt parce qu'il s'utilise
    aussi en ligne de commande ; la console s'en sert pour le bouton de
    configuration usine, sans dupliquer le plan mémoire de l'EEPROM.
    """
    import importlib.util

    path = Path(root) / "tools/mkeeprom.py"
    specification = importlib.util.spec_from_file_location("mkeeprom", path)
    if specification is None or specification.loader is None:
        raise MonitorError(f"générateur d'image EEPROM introuvable : {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@dataclass
class Snapshot:
    """Photographie de la cible à un instant du temps simulé."""

    virtual_time: float = 0.0
    symbols: dict = field(default_factory=dict)     # expr -> entier
    readbacks: dict = field(default_factory=dict)   # expr -> flottant
    outputs: dict = field(default_factory=dict)     # clé sortie -> bool
    pwm: dict = field(default_factory=dict)         # clé PWM -> détail
    factory_page: int | None = None                 # en-tête de la page d'usine
    errors: list = field(default_factory=list)

    @property
    def factory_configured(self) -> bool | None:
        """La page d'usine de l'EEPROM porte-t-elle des données valides ?

        C'est ce que lit `flash_fetch_systeminfo` au démarrage : sans en-tête
        valide, le firmware reste en FACTORY_STATE.
        """
        if self.factory_page is None:
            return None
        return self.factory_page == fw.EEPROM_VALID_PAGE

    @property
    def control_source(self) -> str:
        code = self.readbacks.get(fw.CONTROL_SOURCE)
        if code is None:
            return "—"
        return fw.CONTROL_SOURCES.get(int(code), f"inconnue ({int(code)})")

    @property
    def state(self):
        return self.symbols.get("ier_core.ier_state")

    @property
    def state_name(self) -> str:
        return fw.STATE_NAMES.get(self.state, "—")

    @property
    def last_state_name(self) -> str:
        return fw.STATE_NAMES.get(self.symbols.get("ier_core.ier_last_state"), "—")

    @property
    def alarms(self) -> list:
        """Alarmes levées, niveau 1 puis niveau 2, dans l'ordre des bits."""
        return (fw.decode(self.symbols.get("ier_core.alarm1", 0), fw.ALARM1_FLAGS)
                + fw.decode(self.symbols.get("ier_core.alarm2", 0), fw.ALARM2_FLAGS))

    @property
    def status_flags(self) -> list:
        return fw.decode(self.symbols.get("ier_core.status", 0), fw.STATUS_FLAGS)


class Session:
    """Cycle de vie complet d'une session d'émulation pilotée par la console."""

    def __init__(self, root: Path, elf: Path | None = None,
                 script: Path | None = None, attach_port: int | None = None):
        self.root = Path(root)
        self.elf = Path(elf) if elf else self.root.parent / "ier/Debug/ier.elf"
        self.script = Path(script) if script else self.root / "scripts/ier.resc"
        self.attach_port = attach_port

        self.process: RenodeProcess | None = None
        self.monitor = RenodeMonitor()
        self.symbols: SymbolTable | None = None
        self.resolved: dict = {}
        self.running = False
        self.log_path = self.root / ".renode-console.log"

        self._read_plan: list = []      # (genre, clé, commande)
        self._indirect: dict = {}       # expr -> Indirect
        self._pointers: dict = {}       # adresse du pointeur -> valeur lue
        self._missing: list = []

    # -- ouverture / fermeture --------------------------------------------
    def open(self) -> None:
        self._load_symbols()
        if self.attach_port is not None:
            self.monitor.port = self.attach_port
        else:
            self.process = RenodeProcess(
                self.root / ".renode/renode", self.script, self.log_path)
            self.process.start()
            self.monitor.port = self.process.port
        self.monitor.connect()
        self._build_read_plan()
        self.apply_baseline()

    def close(self) -> None:
        if self.monitor.connected:
            try:
                self.save_eeprom()
            except MonitorError:
                pass        # Renode déjà parti : l'image reste en l'état
        self.monitor.close()
        if self.process is not None:
            self.process.stop()
            self.process = None
        if self.symbols is not None:
            self.symbols.close()
            self.symbols = None

    def _load_symbols(self) -> None:
        self.symbols = SymbolTable(self.elf)
        self.resolved = self.symbols.resolve_all(fw.WATCHED_SYMBOLS)
        self._indirect = self.symbols.resolve_all(fw.WATCHED_INDIRECT)
        self._missing = [e for e in fw.WATCHED_SYMBOLS + fw.WATCHED_INDIRECT
                         if e not in self.resolved and e not in self._indirect]
        if "ier_core.ier_state" not in self.resolved:
            raise SymbolError(
                "ier_core.ier_state introuvable dans l'ELF — la console ne "
                "peut rien observer de la machine à états")

    # -- plan de lecture ---------------------------------------------------
    def _build_read_plan(self) -> None:
        """Rafale unique couvrant tout ce que la fenêtre affiche.

        Grouper les lectures est ce qui rend le sondage périodique gratuit :
        une rafale complète coûte une quinzaine de millisecondes, contre plus
        d'une par commande envoyée isolément.
        """
        plan = []
        for expr, symbol in self.resolved.items():
            command = self.monitor.read_commands([(symbol.address, symbol.width)])[0]
            plan.append(("symbol", expr, command))

        for port in fw.OUTPUT_PORTS:
            address = fw.GPIO_BASE[port] + fw.GPIO_ODR_OFFSET
            plan.append(("odr", port, f"sysbus ReadDoubleWord 0x{address:08X}"))

        for timer in sorted({p.timer for p in fw.PWM_OUTPUTS}):
            base = fw.TIMER_BASE[timer]
            plan.append(("arr", timer,
                         f"sysbus ReadDoubleWord 0x{base + fw.TIMER_ARR_OFFSET:08X}"))
            plan.append(("ccer", timer,
                         f"sysbus ReadDoubleWord 0x{base + _CCER_OFFSET:08X}"))
        for pwm in fw.PWM_OUTPUTS:
            address = fw.TIMER_BASE[pwm.timer] + fw.TIMER_CCR_OFFSET[pwm.channel]
            plan.append(("ccr", pwm.key, f"sysbus ReadDoubleWord 0x{address:08X}"))

        # En-tête de la page d'usine de l'EEPROM : une lecture, qui dit si le
        # firmware a de quoi sortir de FACTORY_STATE.
        plan.append(("factory", "",
                     f"sysbus ReadWord 0x{fw.EEPROM_FACTORY_HEADER:08X}"))

        plan.append(("clock", "", "machine ElapsedVirtualTime"))
        self._read_plan = plan

    def _plan(self) -> list:
        """Plan statique complété des champs joignables via un pointeur.

        `ier_core.param` et `ier_core.database` sont alloués sur le tas au
        démarrage du firmware : leur valeur est relue à chaque rafale, ce qui
        rend le sondage insensible à un reset sans logique de réamorçage.
        """
        plan = list(self._read_plan)
        seen = set()
        for indirect in self._indirect.values():
            address = indirect.pointer.address
            if address in seen:
                continue
            seen.add(address)
            command = self.monitor.read_commands(
                [(address, indirect.pointer.size)])[0]
            plan.append(("pointer", address, command))
        for expr, indirect in self._indirect.items():
            base = self._pointers.get(indirect.pointer.address)
            if not base:
                continue        # pointeur pas encore lu, ou firmware pas parti
            command = self.monitor.read_commands(
                [(indirect.target(base), indirect.size)])[0]
            plan.append(("readback", expr, command))
        return plan

    # -- sondage -----------------------------------------------------------
    def poll(self) -> Snapshot:
        plan = self._plan()
        results = self.monitor.execute_many(cmd for _, _, cmd in plan)
        snapshot = Snapshot()
        odr, arr, ccer, ccr = {}, {}, {}, {}

        for (kind, key, command), result in zip(plan, results):
            if RenodeMonitor.is_error(result):
                snapshot.errors.append(f"{command} : {result.splitlines()[-1]}")
                continue
            if kind == "clock":
                match = _VIRTUAL_TIME.search(result)
                if match:
                    hours, minutes, seconds = match.groups()
                    snapshot.virtual_time = (int(hours) * 3600 + int(minutes) * 60
                                             + float(seconds))
                continue
            value = RenodeMonitor.parse_integer(result)
            if value is None:
                snapshot.errors.append(f"{command} : réponse illisible « {result} »")
                continue
            if kind == "pointer":
                self._pointers[key] = value
                continue
            if kind == "factory":
                snapshot.factory_page = value
                continue
            if kind == "readback":
                indirect = self._indirect[key]
                snapshot.readbacks[key] = (
                    _as_float(value) if indirect.kind == "float" else value)
                continue
            {"symbol": snapshot.symbols, "odr": odr, "arr": arr,
             "ccer": ccer, "ccr": ccr}[kind][key] = value

        for output in fw.DIGITAL_OUTPUTS:
            register = odr.get(output.port)
            if register is not None:
                snapshot.outputs[output.key] = bool(register & (1 << output.bit))

        for pwm in fw.PWM_OUTPUTS:
            period = arr.get(pwm.timer)
            compare = ccr.get(pwm.key)
            enable = ccer.get(pwm.timer)
            if period is None or compare is None:
                continue
            enabled = (enable is not None
                       and bool(enable & (1 << _CCER_ENABLE_BIT[pwm.channel])))
            # Le firmware écrit CCR = PWM_FACTOR × valeur, soit 0 à ARR+1 en
            # PWM mode 1 polarité haute (PWM_init, hal.c). Au-delà de ARR, le
            # matériel ne produit plus aucune correspondance et maintient OCxREF
            # à 1 (RM0316 §22.3.10) : la broche est réellement à 100 %, pas au
            # repos. C'est l'état laissé par l'amorçage TIM_Pulse = 0xFFFF.
            saturated = period > 0 and compare > period
            duty = None
            if period > 0:
                duty = 1.0 if saturated else compare / (period + 1)
            snapshot.pwm[pwm.key] = {
                "duty": duty, "ccr": compare, "arr": period,
                "enabled": enabled, "saturated": saturated,
            }
        return snapshot

    # -- commandes ---------------------------------------------------------
    def apply_baseline(self) -> list:
        """Aligne la cible sur l'état que la console affiche au démarrage."""
        commands = ["runMacro $default_sensors", "runMacro $inputs_idle"]
        self.execute(commands)
        return commands

    def execute(self, commands) -> list:
        commands = [commands] if isinstance(commands, str) else list(commands)
        results = self.monitor.execute_many(commands)
        for command, result in zip(commands, results):
            if RenodeMonitor.is_error(result):
                raise MonitorError(f"{command} : {result.splitlines()[-1]}")
        return results

    @staticmethod
    def analog_command(key: str, value: float) -> str:
        analog = fw.ANALOG_BY_KEY[key]
        return (f"sysbus.adc1 SetVoltage {analog.channel} "
                f"{analog.to_volts(value):.4f}")

    @staticmethod
    def digital_command(key: str, closed: bool) -> str:
        digital = fw.DIGITAL_BY_KEY[key]
        return (f"sysbus.gpioPort{digital.port} OnGPIO {digital.bit} "
                f"{'true' if closed else 'false'}")

    def set_analog(self, key: str, value: float) -> str:
        command = self.analog_command(key, value)
        self.execute(command)
        return command

    def set_digital(self, key: str, closed: bool) -> str:
        command = self.digital_command(key, closed)
        self.execute(command)
        return command

    def run_macro(self, name: str) -> str:
        command = f"runMacro ${name}"
        self.execute(command)
        return command

    def start(self) -> str:
        self.execute("start")
        self.running = True
        return "start"

    def pause(self) -> str:
        self.execute("pause")
        self.running = False
        return "pause"

    def reset(self) -> str:
        """Redémarre la machine, puis réapplique les stimuli par défaut.

        `machine Reset` réinitialise le matériel émulé et exécute la macro
        `reset` de scripts/ier.resc — rechargement de l'ELF, de l'image EEPROM
        et des tensions par défaut.

        Recharger l'ELF seul ne suffit pas : la RAM garde l'état de FreeRTOS,
        et le firmware se fige au bout d'une dizaine de secondes de temps
        simulé au lieu de repartir de son point d'entrée. La pause n'est pas
        facultative non plus, Renode refusant de charger un ELF dans une
        machine en marche.
        """
        self.execute(["pause", "machine Reset", "runMacro $inputs_idle"])
        self._pointers.clear()
        self.running = False
        return "machine Reset"

    # -- EEPROM ------------------------------------------------------------
    @property
    def eeprom_image(self) -> Path:
        """Image lue et réécrite par le contrôleur flash émulé."""
        return self.root / "var/eeprom.bin"

    def save_eeprom(self) -> str:
        """Écrit l'EEPROM courante dans son image.

        Le contrôleur le fait déjà de lui-même à chaque fois que le firmware
        reverrouille la flash ; cet appel ne sert qu'à ne rien perdre si la
        session se termine entre deux écritures.
        """
        self.execute("sysbus.flashCtrl SaveEeprom")
        return "sysbus.flashCtrl SaveEeprom"

    def load_factory_config(self) -> str:
        """Configure la carte en usine, puis redémarre le firmware.

        L'image est patchée plutôt que régénérée : les pages déjà écrites par
        le firmware sont conservées. Le redémarrage est indispensable —
        `flash_fetch_systeminfo` n'est lu qu'au démarrage de la tâche de
        monitoring, injecter la page en cours d'exécution ne changerait rien.
        """
        mkeeprom = _load_mkeeprom(self.root)
        image = self.eeprom_image
        # L'état courant de la cible prime sur le fichier : il peut contenir
        # des pages que le contrôleur n'a pas encore eu l'occasion d'écrire.
        self.execute("pause")
        self.save_eeprom()
        layout = mkeeprom.Layout(self.root.parent / "ier/include/sov_eeflash.h")
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(bytes(mkeeprom.patch_factory(image, layout)))

        # `machine Reset` recharge l'image au passage : le contrôleur flash la
        # relit dans son propre Reset, avant même la macro de scripts/ier.resc.
        self.execute(["machine Reset", "runMacro $inputs_idle"])
        self._pointers.clear()
        self.running = False
        return "machine Reset (configuration usine écrite dans l'EEPROM)"

    @property
    def missing_symbols(self) -> list:
        return list(self._missing)
