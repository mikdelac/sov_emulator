"""Carte des entrées/sorties du firmware IER et décodage de ses vecteurs d'état.

Tout ce qui est décrit ici est relevé dans les sources du firmware, jamais
déduit d'une maquette :

  gpi_update / gpo_update / ADC_init / PWM_init   ../ier/src/hal.c
  évaluation des alarmes                          ../ier/src/task_control.c
  énumérations et masques de bits                 ../ier/include/project_def.h

Note sur la polarité des entrées tout ou rien : elle n'est pas figée dans le
firmware. `task_control.c` choisit entre contact normalement ouvert et
normalement fermé selon le vecteur de configuration `nonc` et selon
`param->hilim_src` / `param->control_src`, tous deux stockés en flash. La
console n'annote donc pas les entrées d'un « fermé = alarme » qui serait faux
une fois sur deux : elle affiche le niveau réel de la broche et, en face, les
bits d'alarme que le firmware a effectivement levés.
"""

from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# Conversion PT1000
# ---------------------------------------------------------------------------
# Points relevés dans adc_conv (hal.c, branche IERPCB001v3_0) :
#   R = 908.46897 * V - 444.4657
#   T = -41.6125 * (sqrt(7612.47 - R) - 81.3171)
PT1000_CURVE = [
    (1.500, -21.0),
    (1.560, -7.0),
    (1.612, 5.0),
    (1.676, 20.0),
    (1.846, 60.0),
    (2.014, 100.0),
]


def temperature_to_volts(celsius: float) -> float:
    """Tension à présenter à l'ADC pour lire `celsius` sur une entrée PT1000."""
    if celsius <= PT1000_CURVE[0][1]:
        return PT1000_CURVE[0][0]
    for (v0, t0), (v1, t1) in zip(PT1000_CURVE, PT1000_CURVE[1:]):
        if celsius <= t1:
            return v0 + (v1 - v0) * (celsius - t0) / (t1 - t0)
    return PT1000_CURVE[-1][0]


def volts_to_temperature(volts: float) -> float:
    """Réciproque de `temperature_to_volts`, pour relire un canal PT1000."""
    if volts <= PT1000_CURVE[0][0]:
        return PT1000_CURVE[0][1]
    for (v0, t0), (v1, t1) in zip(PT1000_CURVE, PT1000_CURVE[1:]):
        if volts <= v1:
            return t0 + (t1 - t0) * (volts - v0) / (v1 - v0)
    return PT1000_CURVE[-1][1]


# ---------------------------------------------------------------------------
# Entrées analogiques — séquence programmée par ADC_init (hal.c)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AnalogInput:
    key: str
    pin: str
    channel: int
    label: str
    unit: str
    minimum: float
    maximum: float
    step: float
    default: float
    to_volts: Callable[[float], float]
    decimals: int = 0
    note: str = ""


ANALOG_INPUTS = [
    AnalogInput(
        "wl", "PA0", 1, "Niveau d'eau", "V capteur",
        0.5, 5.0, 0.05, 3.00, lambda v: v * 0.30, decimals=2,
        note="Flotteur MOJO 0,5-5 V · V_adc = V_capteur × 0,30",
    ),
    AnalogInput(
        "demand", "PA1", 2, "Demande analogique", "%",
        0.0, 100.0, 1.0, 0.0, lambda v: v * 0.03,
        note="Entrée 0-10 V ramenée à 3,0 V · V_adc = % × 0,03",
    ),
    AnalogInput(
        "hum", "PA2", 3, "Humidité de gaine", "%",
        0.0, 100.0, 1.0, 50.0, lambda v: v * 0.03,
    ),
    AnalogInput(
        "water", "PA3", 4, "PT1000 eau", "°C",
        -21.0, 100.0, 1.0, 20.0, temperature_to_volts,
        note="Courbe PT1000 en racine carrée, compensée Vrefint",
    ),
    AnalogInput(
        "hilim_a", "PF4", 5, "Limite haute analogique", "%",
        0.0, 100.0, 1.0, 0.0, lambda v: v * 0.03,
    ),
    AnalogInput(
        "cab", "PC2", 8, "PT1000 coffret", "°C",
        -21.0, 100.0, 1.0, 20.0, temperature_to_volts,
        note="même courbe que l'eau, comme la macro water_* de sensors.resc ; "
             "ier_core.database->et reste à 0 dans cette configuration, la "
             "conversion du canal 8 n'est donc pas observable",
    ),
]

ANALOG_BY_KEY = {a.key: a for a in ANALOG_INPUTS}

# Canaux réglés une fois pour toutes au reset, jamais exposés dans l'IHM.
# Le canal 18 ne doit jamais valoir 0 : adc_conv divise par sa moyenne.
FIXED_CHANNELS = {16: 1.43, 18: 1.23}

# Étalonnage usine relevé par ST, lu par hal.c:394-396.
FACTORY_CALIBRATION = [
    (0x1FFFF7B8, 1750),   # TS_CAL1 (30 °C)
    (0x1FFFF7BA, 1526),   # VREFINT_CAL (1,23 V)
    (0x1FFFF7C2, 1400),   # TS_CAL2 (110 °C)
]


# ---------------------------------------------------------------------------
# Entrées tout ou rien — gpi_update, branche IERPCB001v2_10
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DigitalInput:
    key: str
    pin: str
    port: str
    bit: int
    label: str
    flag: str       # bit positionné dans le vecteur gpi
    alarm: str      # alarme de alarm1 que cette entrée alimente
    default: bool = False


DIGITAL_INPUTS = [
    DigitalInput("enable", "PB8", "B", 8, "Enable contact",
                 "ENA_FLAG_IN", "ENABLE_SWITCH_AL"),
    DigitalInput("airflow", "PB9", "B", 9, "Air flow switch",
                 "AFS_FLAG_IN", "AIR_FLOW_ERR_AL"),
    DigitalInput("hilim", "PF9", "F", 9, "Hi limit humidistat",
                 "HLH_FLAG_IN", "HIGH_RH_IN_DUCT_AL"),
    DigitalInput("foam", "PF10", "F", 10, "Détecteur de mousse",
                 "FS_FLAG_IN", "FOAM_DET_AL"),
]

DIGITAL_BY_KEY = {d.key: d for d in DIGITAL_INPUTS}


# ---------------------------------------------------------------------------
# Sorties tout ou rien — gpo_update (hal.c)
# ---------------------------------------------------------------------------
GPIO_BASE = {
    "A": 0x48000000, "B": 0x48000400, "C": 0x48000800,
    "D": 0x48000C00, "E": 0x48001000, "F": 0x48001400,
}
GPIO_ODR_OFFSET = 0x14


@dataclass(frozen=True)
class DigitalOutput:
    key: str
    pin: str
    port: str
    bit: int
    label: str
    flag: str
    note: str = ""
    warning: bool = False       # allumé = anomalie, pas un état normal


DIGITAL_OUTPUTS = [
    DigitalOutput("contactor", "PD5", "D", 5, "Contacteur (C1)", "C1_FLAG_OUT"),
    DigitalOutput("fill", "PD7", "D", 7, "Électrovanne de remplissage (FV1)",
                  "FV1_FLAG_OUT"),
    DigitalOutput("pump", "PD6", "D", 6, "Pompe de vidange", "PUMP_FLAG_OUT"),
    DigitalOutput("spare_ac", "PD4", "D", 4, "Sortie AC de réserve",
                  "FOAMACDRIVE_FLAG_OUT", note="vanne à bille déportée"),
    DigitalOutput("op_enable", "PE14", "E", 14, "Contact Operation enable",
                  "OPEN_FLAG_OUT"),
    DigitalOutput("alarm_out", "PE13", "E", 13, "Contact Alarm enable",
                  "ALEN_FLAG_OUT", warning=True),
    DigitalOutput("enclosure_fan", "PE12", "E", 12, "Ventilateur de coffret (ECF)",
                  "ECF_FLAG_OUT"),
    DigitalOutput("health", "PE8", "E", 8, "LED santé (clignote)", "ON_FLAG_OUT"),
]

OUTPUT_PORTS = sorted({o.port for o in DIGITAL_OUTPUTS})


# ---------------------------------------------------------------------------
# Sorties PWM — PWM_init (hal.c)
# ---------------------------------------------------------------------------
TIMER_BASE = {"TIM4": 0x40000800, "TIM8": 0x40013400}
TIMER_ARR_OFFSET = 0x2C
TIMER_CCR_OFFSET = {1: 0x34, 2: 0x38, 3: 0x3C, 4: 0x40}


@dataclass(frozen=True)
class PwmOutput:
    key: str
    pin: str
    timer: str
    channel: int
    label: str
    note: str = ""


PWM_OUTPUTS = [
    PwmOutput("ssr", "PC6", "TIM8", 1, "SSR élément chauffant",
              note="TIM8_CH1 · AF4"),
    PwmOutput("blower", "PC7", "TIM8", 2, "Ventilateur externe (SBX)",
              note="TIM8_CH2 · AF4"),
    PwmOutput("demand_out", "PD14", "TIM4", 1, "Recopie de demande 0-10 V",
              note="TIM4_CH1 · AF2"),
    PwmOutput("aux_out", "PD15", "TIM4", 4, "Sortie auxiliaire 0-10 V",
              note="TIM4_CH4 · AF2"),
]


# ---------------------------------------------------------------------------
# Machine à états — ier_state_e (project_def.h)
# ---------------------------------------------------------------------------
STATES = [
    (0, "STANDBY_STATE", "en attente d'activation"),
    (1, "ARMED_STATE", "prêt, attend une demande"),
    (2, "STEAM_ON_STATE", "production de vapeur"),
    (3, "DRAINCYCLE_STATE", "cycle de vidange"),
    (4, "ADD_WATER_STATE", "remplissage"),
    (5, "PRE_HEAT_STATE", "préchauffage (si PREHEAT_CONFIG)"),
    (6, "ALARMS_STATE", "alarme active"),
    (7, "DEBUG_STATE", "mode debug"),
    (8, "SERVICE_STATE", "vidange + refroidissement"),
    (9, "FACTORY_STATE", "attente de réglages usine"),
    (10, "ANTI_FREEZE_STATE", "protection hors-gel"),
]

STATE_NAMES = {n: name for n, name, _ in STATES}


def _flags(pairs):
    return [(1 << i, name) for i, name in pairs if name]


# alarm1 — IER LEVEL 1 PRIORITY ALARMS
ALARM1_FLAGS = _flags(enumerate([
    "ET_SENSOR_DEF_AL", "ET_TEMP_TOO_HOT", "WL_SENSOR_DEF_AL",
    "WL_SENSOR_ERR_AL", "WL_TOO_HIGH_AL", "WT_SENSOR_DEF_AL",
    "WT_SENSOR_ERR_AL", "FOAM_DET_AL", "HIGH_RH_IN_DUCT_AL",
    "AIR_FLOW_ERR_AL", "ENABLE_SWITCH_AL", "WATER_FEED_ERR_AL",
    "DRAIN_PUMP_ERR_AL", "CRITICAL_TANK_FREEZING_AL",
    "CRITICAL_ET_FREEZING_AL", "WT_TOO_HIGH_AL",
]))

# alarm2 — IER LEVEL 2 ALARMS (avertissements)
ALARM2_FLAGS = _flags(enumerate([
    "WL_TOO_LOW_AL", None, "TANK_FREEZING_HAZARD_AL", "ET_FREEZING_HAZARD_AL",
    "WATER_INLET_FLOW_AL", "ET_HOT_AL", "HIGH_TEMP_SSW_AL", "POWER_SHUTDOWN_AL",
    "ELECTRIC_SUPPLY_AL", "NO_CTL_CONNECTED_AL", "SERVICE_AL",
    "IER_NOT_HEATING", "SPI_COMM_LOST_AL", None, None, "ALL_ALARMS2_AL",
]))

STATUS_FLAGS = _flags(enumerate([
    "ON", "NO_FACTORY_SETTING", "STEAM_ON", "DRAIN_CYCLE", "DRAINING",
    "ADD_WATER", "DEBUG", "SERVICE", "HEATING", "AUTO_DILUTION", "SBX_ON",
    "HEATING_STEAM_NEEDED", "ANTI_FREEZE", "SCREEN_CONTROL", "BMS_CONTROL",
    "DISINFECTION",
]))


def decode(vector: int, table) -> list[str]:
    """Noms des bits levés dans `vector`, dans l'ordre des poids croissants."""
    return [name for mask, name in table if vector & mask]


# ---------------------------------------------------------------------------
# Macros — reprises telles quelles de scripts/sensors.resc
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Macro:
    name: str
    patch: dict = field(default_factory=dict)


MACROS = [
    # default_sensors ne repositionne que les tensions : dans sensors.resc
    # elle ne touche pas aux entrées tout ou rien, la console non plus.
    Macro("default_sensors", {
        "wl": 3.00, "demand": 0, "hum": 50, "water": 20, "hilim_a": 0,
        "cab": 20,
    }),
    Macro("inputs_running", {"enable": True, "airflow": True,
                             "hilim": False, "foam": False}),
    Macro("inputs_idle", {"enable": False, "airflow": False,
                          "hilim": False, "foam": False}),
    Macro("enable_on", {"enable": True}),
    Macro("enable_off", {"enable": False}),
    Macro("airflow_on", {"airflow": True}),
    Macro("airflow_off", {"airflow": False}),
    Macro("demand_0", {"demand": 0}),
    Macro("demand_50", {"demand": 50}),
    Macro("demand_100", {"demand": 100}),
    Macro("tank_empty", {"wl": 0.50}),
    Macro("tank_full", {"wl": 5.00}),
    Macro("water_cold", {"water": 5}),
    Macro("water_warm", {"water": 20}),
    Macro("water_hot", {"water": 60}),
    Macro("water_boiling", {"water": 100}),
]

# Symboles du firmware lus à chaque sondage. Les adresses sont résolues dans
# le DWARF de l'ELF (console/symbols.py), jamais codées en dur.
WATCHED_SYMBOLS = [
    "ier_core.ier_state",
    "ier_core.ier_last_state",
    "ier_core.alarm0",
    "ier_core.alarm1",
    "ier_core.alarm2",
    "ier_core.status",
    "ier_core.core_status",
    "ier_core.config",
]


# ---------------------------------------------------------------------------
# Relectures du firmware — ier_core.database, alloué par pvPortMalloc
# ---------------------------------------------------------------------------
# Boucler la boucle : la console injecte des tensions, ces champs disent ce que
# le firmware en a fait après conversion (adc_conv) et filtrage. Un écart entre
# les deux colonnes signale une erreur d'étalonnage ou de câblage, pas un bug
# de l'émulation.
@dataclass(frozen=True)
class Readback:
    expr: str
    label: str
    unit: str
    decimals: int = 1


READBACKS = [
    Readback("ier_core.database->wl", "Niveau d'eau", "%"),
    Readback("ier_core.database->wt", "Température d'eau", "°C"),
    Readback("ier_core.database->et", "Température coffret", "°C"),
    Readback("ier_core.database->rh", "Humidité de gaine", "%"),
    Readback("ier_core.database->ad", "Demande analogique filtrée", "%"),
    Readback("ier_core.database->demand", "Demande calculée", "%"),
    Readback("ier_core.database->setpoint", "Consigne", "%"),
    Readback("ier_core.database->output", "Sortie de régulation", "%"),
    Readback("ier_core.database->output_ssr", "Sortie SSR", "%"),
    Readback("ier_core.database->uctemp", "Température µC", "°C"),
]

# La source de commande décide si la demande analogique est seulement lue.
# En SCREEN_C_SRC ou COMM_C_SRC, agir sur PA1 depuis la console ne produit
# rien : c'est l'écran ou le maître Modbus qui commande.
CONTROL_SOURCE = "ier_core.param->control_src"

CONTROL_SOURCES = {
    0: "EXT_C_SRC · demande analogique externe",
    1: "INT_RH_C_SRC · humidité interne",
    2: "INT_TEMP_C_SRC · température interne",
    3: "ON_OFF_C_SRC · tout ou rien",
    4: "COMM_C_SRC · BMS / Modbus",
    5: "DISINFECTION_C_SRC · désinfection",
}

WATCHED_INDIRECT = [CONTROL_SOURCE] + [r.expr for r in READBACKS]
