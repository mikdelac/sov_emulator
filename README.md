# Émulation Renode du firmware IER (STM32F303xC)

Fait tourner le firmware de régulation IER (`../ier`, FreeRTOS) sans carte, dans
[Renode](https://renode.io). L'ordonnanceur démarre, toutes les tâches
s'exécutent, et les valeurs de capteurs (ADC et entrées tout ou rien) s'injectent
depuis la console pour observer le comportement de la régulation.

## Démarrage

```bash
./tools/install-renode.sh      # Renode dans .renode/, sans droits root
make -C ../ier/Debug           # firmware avec symboles et traces semihosting
./run.sh                       # console Renode
```

Dans la console Renode :

```
(ier) start                    # lance l'exécution
(ier) pause
(ier) runMacro $inputs_running # ENABLE fermé + débit d'air : sort de l'état alarme
(ier) runMacro $demand_50      # demande analogique à 50 %
(ier) sysbus.adc1 SetVoltage 4 1.846    # eau à ~60 °C
```

Debug pas à pas :

```bash
./run.sh --debug                                    # serveur GDB sur :3333
gdb-multiarch ../ier/Debug/ier.elf -ex 'target remote :3333'
```

Non-régression :

```bash
./test.sh                      # 5 tests Robot (~80 s)
```

## Console graphique

`./console.sh` ouvre une console PyQt6 qui démarre Renode, pilote les entrées
et affiche l'état réel du firmware. Elle remplace la saisie de commandes du
Monitor pour tout ce qui est stimulation de capteurs et observation.

```bash
./console.sh                   # démarre Renode et ouvre la fenêtre
./console.sh --attach 12345    # rejoint un Renode lancé avec « -P 12345 »
./console.sh --self-check      # vérifie la cohérence, sans fenêtre
./console.sh --interval 300    # période de sondage, en millisecondes
```

Les dépendances (PyQt6, pyelftools) s'installent au premier lancement dans
`.venv-console/`, séparé du `.venv/` des tests Robot.

**Rien n'y est simulé côté console.** Les curseurs et les bascules envoient les
mêmes commandes que `scripts/sensors.resc` ; tout ce qui est affiché en retour
est lu dans la cible à chaque sondage :

| Affichage | Source lue |
|---|---|
| machine à états, alarmes, statut | `ier_core` dans la SRAM, adresses résolues dans le DWARF de l'ELF |
| relectures capteurs, demande, sortie | `ier_core.database`, déréférencé à l'exécution |
| sorties tout ou rien | registres `GPIOx_ODR` des ports D et E |
| sorties PWM | `TIMx_CCR` rapporté à `ARR`, activation lue dans `CCER` |
| source de commande | `ier_core.param->control_src` |
| horloge | `machine ElapsedVirtualTime` |

Les adresses ne sont jamais codées en dur : `console/symbols.py` les extrait du
DWARF au démarrage, y compris les champs atteints par pointeur. Un firmware
recompilé avec des structures modifiées reste donc lisible sans rien changer.

**Les effets ne sont pas immédiats.** Le firmware filtre les entrées tout ou
rien sur 25 échantillons pris toutes les 250 ms : il faut laisser passer une
quinzaine de secondes de temps simulé avant de conclure. Une bascule lue trop
tôt donne un état transitoire — contacts fermés, la console affiche brièvement
`ARMED_STATE` sans alarme, puis l'état définitif une fois les filtres pleins.
Les commandes envoyées apparaissent tout de suite dans le journal, leurs effets
quand le firmware les a vus.

Le sondage coûte de la vitesse de simulation, une rafale complète faisant une
trentaine de lectures groupées :

| Période de sondage | Vitesse de simulation |
|---|---|
| saturation (sans pause) | 0,67 × temps réel |
| 150 ms (défaut) | 0,88 × temps réel |
| 500 ms | 1,00 × temps réel |

`--self-check` relit `scripts/sensors.resc` et vérifie que les seize macros de
la console produisent exactement les mêmes commandes, puis que tous les
symboles attendus existent dans l'ELF. C'est ce qui empêche l'interface de
diverger silencieusement des scripts.

### Étalonnage usine absent : ce que ça change

La flash non chargée rend `0x0000` sous Renode. Or `VALID_PAGE` vaut aussi
`0x0000` (`sov_eeflash.h`), donc `flash_fetch_systeminfo()` croit lire une page
usine valide et récupère tous les offsets à zéro. Deux conséquences visibles
dans la console, qui ne sont ni des bugs du firmware ni des défauts de
l'émulation :

- `nonc_input_vector = 0` — tous les contacts sont pris pour normalement
  fermés. C'est ce qui rend la polarité décrite ci-dessous vraie *pour cette
  configuration* ; une unité programmée en normalement ouvert se comporterait à
  l'inverse.
- `wt_offset = 0 − 10 = −10 °C` (`sov_eeflash.c:209`, la valeur est biaisée de
  +10 pour tenir dans un mot non signé). `task_control.c:174` l'ajoute, donc
  `ier_core.database->wt` affiche **10 °C de moins** que l'échantillon converti
  `ier_input.cadc->wt`. La console montre les deux côte à côte : un écart
  constant de 10 °C entre « échantillon » et valeur filtrée vient de là, pas
  d'une erreur de courbe.

Sur une carte réelle à flash effacée on lirait `0xFFFF`, donc `NO_VALID_PAGE`,
et le firmware partirait en `FACTORY_STATE`.

### Polarité des entrées tout ou rien

La console n'annote pas les entrées d'un « fermé = alarme » : `task_control.c`
choisit entre contact normalement ouvert et normalement fermé selon un vecteur
de configuration stocké en flash. Elle affiche le niveau de la broche d'un côté
et les alarmes effectivement levées de l'autre. Avec les paramètres par défaut
du firmware et les filtres stabilisés, contacts ENABLE, débit d'air et limite
haute **fermés** donnent `ARMED_STATE` sans alarme ; ouvrir l'un des trois lève
l'alarme correspondante.

## Ce qui est émulé

Cible : **STM32F303xC** — Cortex-M4F à 72 MHz, 256 Ko de flash, 40 Ko de SRAM,
8 Ko de CCMRAM, déduits de `../ier/ldscripts/mem.ld` et des options de
compilation.

| Bloc | Rôle dans le firmware | Modèle |
|---|---|---|
| ADC1 + séquenceur | 9 conversions, mode continu | `peripherals/IER_STM32F3_ADC.cs` |
| DMA1 ch1 | ADC1 vers `adc1_dma_value` | `DMA.STM32LDMA` |
| RCC | horloges, LSI/LSE, PLL | `peripherals/STM32F3_RCC.cs` |
| Interface flash | déverrouillage et effacement pour `sov_eeflash` | `peripherals/IER_STM32F3_FlashController.cs` |
| CRC matériel | CRC16-CCITT des trames SPI | `CRC.STM32_CRC` (série F0, polynôme programmable) |
| USART1/2/3 | Modbus RTU et liaison de service | `UART.STM32F7_USART` |
| TIM1/2/3/4/8 | PWM des SSR, tick API, pile Modbus | `Timers.STM32_Timer` |
| GPIO A à F, EXTI, IWDG, RTC, SPI2 | | modèles Renode standard |
| Bit-band périphérique et SRAM | `RCC_LSICmd`, `PWR_BackupAccessCmd`... | `Miscellaneous.BitBanding` |

Trois modèles ont dû être écrits parce que Renode n'en fournit pas d'équivalent
utilisable pour le F3 ; chaque fichier de `peripherals/` explique en tête ce qui
manquait et pourquoi.

## Injecter des valeurs de capteurs

Les tensions se règlent à chaud, en volts vus par l'ADC :

```
sysbus.adc1 SetVoltage <canal> <volts>
sysbus.adc1 GetVoltage <canal>
```

| Canal | Broche | Grandeur | Conversion appliquée par le firmware |
|---|---|---|---|
| 1 | PA0 | niveau d'eau | `V_adc = tension_capteur × 0,30` |
| 2 | PA1 | demande analogique | `V_adc = pourcentage × 0,03` |
| 3 | PA2 | humidité de gaine | `V_adc = pourcentage × 0,03` |
| 4 | PA3 | température d'eau (PT1000) | courbe en racine carrée, voir ci-dessous |
| 5 | PF4 | limite haute | `V_adc = pourcentage × 0,03` |
| 8 | PC2 | température du coffret (PT1000) | idem canal 4, **tension × 3,3/3,0** |
| 16 | — | température interne du µC | |
| 18 | — | Vrefint — **ne jamais mettre à 0** | `adc_conv` divise par cette valeur |

Points de la courbe PT1000 (`adc_conv`, branche `IERPCB001v3_0`) :

| Tension | 1,500 V | 1,560 V | 1,612 V | 1,676 V | 1,846 V | 2,014 V |
|---|---|---|---|---|---|---|
| Température | −21 °C | −7 °C | 5 °C | 20 °C | 60 °C | 100 °C |

**Le canal 8 se règle sur cette table multipliée par 3,3/3,0.** La formule
PT1000 suppose VDDA = 3,0 V (coefficient `0.0007326` = 3,0/4095). Sur la
température d'eau, `hal.c:2042` rattrape l'écart par un facteur 3,3/3,0 ; sur
la température de coffret, `hal.c:2243` ne l'applique pas. Avec
`referenceVoltage: 3.3` dans le `.repl`, présenter 1,676 V sur le canal 8 fait
donc lire −15 °C au firmware, pas 20 °C.

`ier_core.database->et` ne permet pas de le vérifier : son affectation est sous
`#ifdef OUTSIDE_ENCLOSURE`, désactivé, ou conditionnée à
`control_src == DISINFECTION_C_SRC`. Le champ reste à 0. L'échantillon converti
se lit dans `ier_input.cadc->et[ier_input.idx]`, que la console affiche.

Entrées tout ou rien (`gpi_update`, branche `IERPCB001v2_10`) :

| Broche | Signal | Commande |
|---|---|---|
| PB8 | ENABLE (son absence met la machine en alarme) | `sysbus.gpioPortB OnGPIO 8 true` |
| PB9 | contrôleur de débit d'air | `sysbus.gpioPortB OnGPIO 9 true` |
| PF9 | humidistat de limite haute en gaine | `sysbus.gpioPortF OnGPIO 9 true` |
| PF10 | détecteur de mousse | `sysbus.gpioPortF OnGPIO 10 true` |

`scripts/sensors.resc` regroupe des macros prêtes à l'emploi : `default_sensors`
(appliquée au reset), `inputs_running`, `inputs_idle`, `enable_on`/`enable_off`,
`airflow_on`/`airflow_off`, `demand_0`/`demand_50`/`demand_100`,
`tank_empty`/`tank_full`, `water_cold`/`water_warm`/`water_hot`/`water_boiling`.

Un échantillon est pris toutes les 250 ms et le buffer circulaire compte 50
entrées : après un changement, il faut au moins 250 ms de temps simulé pour voir
l'effet, et 12,5 s pour que tout le buffer soit à jour.

## Observer ce que fait le firmware

Avec GDB (`./run.sh --debug`), les points d'observation utiles :

```
print ier_core.ier_state          # état de la machine d'états
print ier_core.ier_last_state
print ier_input.idx               # avance de la tâche de lecture des capteurs
print adc1_dma_value              # 9 valeurs brutes, dans l'ordre de la séquence
print ier_input.cadc[0].wt[0]@10  # températures d'eau converties
print xTickCount
```

Sans GDB, depuis la console Renode :

```
sysbus GetSymbolAddress "xTickCount"
sysbus ReadDoubleWord <adresse>
```

Au démarrage, avec les entrées à zéro, le firmware passe en `ALARMS_STATE` :
c'est le comportement attendu, l'entrée ENABLE étant configurée en alarme
(`USE_ENABLE_SWITCH_AS_ALARM`). `runMacro $inputs_running` le ramène en
`STANDBY_STATE`.

Les `trace_puts` du firmware sortent en semihosting dans le journal Renode, mais
la plupart sont commentés dans les sources : il faut les décommenter et
recompiler pour les voir.

## Limites connues

- **SPI2 esclave non simulé.** Renode ne modélise le SPI qu'en maître ; le lien
  avec l'écran 7 pouces (trames de 8 octets, CRC16-CCITT) n'a donc pas de trafic.
  Rejouer des trames capturées demanderait un modèle esclave dédié.
- **Aucun maître Modbus.** Les trames émises sur USART1 sont visibles dans
  l'analyseur, mais rien ne répond côté hôte.
- **Temporisations approchées.** Les PWM des SSR (TIM1/TIM8) et la pile Modbus
  (TIM3) tournent à des fréquences approchées : les mesures de temps fines ne
  sont pas représentatives du matériel.
- **RCC, RTC et IWDG fonctionnels, pas fidèles au cycle près** — les bits d'état
  suivent immédiatement les commandes, sans temps de stabilisation.
- Quelques avertissements subsistent au démarrage sur des bits non modélisés
  (SPI2 `CR2.DS`, TIM8 `BDTR.MOE`, RTC `CR.TSE`, priorité NVIC de l'IRQ 16) :
  aucun n'affecte l'exécution.

## Organisation

```
platforms/ier_stm32f303.repl   description de la carte et du SoC
peripherals/*.cs               modèles compilés à la volée par Renode
scripts/ier.resc               script principal
scripts/sensors.resc           stimuli capteurs et entrées TOR
scripts/ier-debug.resc         idem + serveur GDB
tests/ier-boot.robot           non-régression
tools/install-renode.sh        installation locale de Renode (version épinglée)
run.sh / test.sh               lanceurs

console/firmware.py            carte des E/S et vecteurs d'état, relevés dans ../ier
console/symbols.py             résolution des adresses dans le DWARF de l'ELF
console/monitor.py             client TCP du Monitor Renode, lectures groupées
console/session.py             plan de lecture et construction des commandes
console/worker.py              sondage hors du fil de l'interface
console/widgets.py             composants dessinés d'après le design system
console/window.py              fenêtre principale
console/theme.py               jetons de style steamOvap
console/selfcheck.py           cohérence console / sensors.resc / ELF
console.sh                     lanceur de la console
```

La version de Renode est épinglée dans `tools/install-renode.sh`. C'est une
nightly : la 1.16.1 stable ne fait pas implémenter `IDMA` à `DMA.STM32LDMA`, ce
qui empêche l'ADC de déclencher les transferts DMA.
