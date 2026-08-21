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
./test.sh                      # 10 tests Robot (~130 s)
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
| état de l'EEPROM | en-tête de la page d'usine, lu dans la flash émulée |
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

### Polarité des entrées tout ou rien

La console n'annote pas les entrées d'un « fermé = alarme » : `task_control.c`
choisit entre contact normalement ouvert et normalement fermé selon un vecteur
de configuration stocké en flash. Elle affiche le niveau de la broche d'un côté
et les alarmes effectivement levées de l'autre. Avec les paramètres par défaut
du firmware et les filtres stabilisés, contacts ENABLE, débit d'air et limite
haute **fermés** donnent `ARMED_STATE` sans alarme ; ouvrir l'un des trois lève
l'alarme correspondante.

## EEPROM émulée

Le firmware range ses réglages dans les onze dernières pages de la flash
(`0x08030000`, `sov_eeflash.c`) : dix pages tournantes pour les paramètres de
régulation, une onzième pour les informations d'usine — numéro de série,
modèle, étalonnages, et le vecteur NO/NC qui décide de la polarité des entrées
tout ou rien.

Cette zone se comporte comme la flash d'une vraie carte. Elle démarre à `0xFF`,
comme une flash effacée, et **ce que le firmware y écrit est conservé** d'une
session à l'autre dans `var/eeprom.bin`. Le contrôleur flash émulé écrit
l'image de lui-même, chaque fois que le firmware reverrouille la flash après
avoir réécrit une page ; rien n'est sondé et la vitesse de simulation n'en
souffre pas.

Une carte neuve n'a pas d'informations d'usine : le firmware démarre en
`FACTORY_STATE` et y reste. Sur le matériel, c'est l'écran 7 pouces qui l'en
sort, par un lien SPI que Renode ne modélise pas — le bouton **« Configuration
usine »** de la console écrit donc la page d'usine dans l'EEPROM et redémarre
le firmware. Le vecteur NO/NC qu'il installe vaut zéro, soit toutes les entrées
normalement fermées : la polarité décrite plus haut reste vraie.

L'image se fabrique aussi en ligne de commande, sans lancer Renode :

```bash
python3 tools/mkeeprom.py --blank   var/eeprom.bin   # carte neuve
python3 tools/mkeeprom.py --factory var/eeprom.bin   # carte configurée
python3 tools/mkeeprom.py --patch   var/eeprom.bin   # ajoute l'usine sans
                                                     # perdre les pages écrites
```

Les offsets ne sont jamais recopiés : `tools/mkeeprom.py` relit le plan mémoire
dans `../ier/include/sov_eeflash.h` à chaque exécution, et `./console.sh
--self-check` vérifie que la console et la plateforme s'accordent avec lui. Le
fichier `var/eeprom.bin` est propre à chaque poste et n'est pas suivi par git ;
le supprimer rend la carte neuve.

Depuis le Monitor, trois macros complètent le tableau :

```
(ier) runMacro $eeprom_save      # force l'écriture de l'image
(ier) runMacro $eeprom_reload    # relit l'image dans la cible
(ier) runMacro $eeprom_erase     # remet les onze pages à 0xFF
```

Un rechargement d'image n'a d'effet qu'au démarrage suivant : le firmware ne
lit sa flash qu'au lancement de sa tâche de monitoring. Faire suivre d'un
`machine Reset`.

## Ce qui est émulé

Cible : **STM32F303xC** — Cortex-M4F à 72 MHz, 256 Ko de flash, 40 Ko de SRAM,
8 Ko de CCMRAM, déduits de `../ier/ldscripts/mem.ld` et des options de
compilation.

| Bloc | Rôle dans le firmware | Modèle |
|---|---|---|
| ADC1 + séquenceur | 9 conversions, mode continu | `peripherals/IER_STM32F3_ADC.cs` |
| DMA1 ch1 | ADC1 vers `adc1_dma_value` | `DMA.STM32LDMA` |
| RCC | horloges, LSI/LSE, PLL | `peripherals/STM32F3_RCC.cs` |
| Interface flash | déverrouillage, effacement et EEPROM persistante de `sov_eeflash` | `peripherals/IER_STM32F3_FlashController.cs` |
| CRC matériel | CRC16-CCITT des trames SPI | `CRC.STM32_CRC` (série F0, polynôme programmable) |
| USART1/2/3 | Modbus RTU et liaison de service | `UART.STM32F7_USART` |
| TIM4, TIM8 | PWM du SSR, de la soufflante SBX et recopie 0-10 V | `peripherals/IER_STM32_TimerPWM.cs` |
| TIM1/2/3 | tick API, pile Modbus | `Timers.STM32_Timer` |
| GPIO A à F, EXTI, IWDG, RTC, SPI2 | | modèles Renode standard |
| Bit-band périphérique et SRAM | `RCC_LSICmd`, `PWR_BackupAccessCmd`... | `Miscellaneous.BitBanding` |

Quatre modèles ont dû être écrits parce que Renode n'en fournit pas d'équivalent
utilisable pour le F3 ; chaque fichier de `peripherals/` explique en tête ce qui
manquait et pourquoi.

Le dernier, `IER_STM32_TimerPWM.cs`, comble le trou le plus lourd de conséquences :
`Timers.STM32_Timer` ne modélise que la base de temps, ses registres `CCR1..CCR4`
ne mémorisent rien et `CCxIF` n'est jamais levé. Or `TIM8_CC_IRQHandler` et
`TIM4_IRQHandler` sont les seuls appelants de `PWM_write()` dans le firmware,
donc les seuls écrivains de `CCR` : avec le modèle intégré, le SSR de l'élément
chauffant, la soufflante SBX et la recopie 0-10 V restaient figés pour toute la
session, la régulation calculant un rapport cyclique que rien ne transmettait.
Le rapport cyclique effectivement présenté sur une broche se relit désormais
depuis le moniteur :

```
sysbus.timer8 GetDutyCycle 1     # SSR, PC6
sysbus.timer8 GetDutyCycle 2     # soufflante SBX, PC7
sysbus.timer4 GetDutyCycle 4     # recopie 0-10 V, PD15
```

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
| 8 | PC2 | température du coffret (PT1000) | idem canal 4 |
| 16 | — | température interne du µC | |
| 18 | — | Vrefint — **ne jamais mettre à 0** | `adc_conv` divise par cette valeur |

Points de la courbe PT1000 (`adc_conv`, branche `IERPCB001v3_0`) :

| Tension | 1,500 V | 1,560 V | 1,612 V | 1,676 V | 1,846 V | 2,014 V |
|---|---|---|---|---|---|---|
| Température | −21 °C | −7 °C | 5 °C | 20 °C | 60 °C | 100 °C |

Entrées tout ou rien (`gpi_update`, branche `IERPCB001v2_10`) :

| Broche | Signal | Commande |
|---|---|---|
| PB8 | ENABLE (son absence met la machine en alarme) | `sysbus.gpioPortB OnGPIO 8 true` |
| PB9 | contrôleur de débit d'air | `sysbus.gpioPortB OnGPIO 9 true` |
| PF9 | niveau haut matériel | `sysbus.gpioPortF OnGPIO 9 true` |
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

Sur une EEPROM vierge, le firmware démarre en `FACTORY_STATE` : il attend sa
configuration d'usine, comme une carte sortie de production. Le bouton
« Configuration usine » de la console l'en sort (voir *EEPROM émulée*).

Une fois la carte configurée, avec les entrées à zéro, le firmware passe en
`ALARMS_STATE` : c'est le comportement attendu, l'entrée ENABLE étant
configurée en alarme (`USE_ENABLE_SWITCH_AS_ALARM`). `runMacro $inputs_running`
le ramène en `STANDBY_STATE`.

Les `trace_puts` du firmware sortent en semihosting dans le journal Renode, mais
la plupart sont commentés dans les sources : il faut les décommenter et
recompiler pour les voir.

## Limites connues

- **SPI2 esclave non simulé.** Renode ne modélise le SPI qu'en maître ; le lien
  avec l'écran 7 pouces (trames de 8 octets, CRC16-CCITT) n'a donc pas de trafic.
  Rejouer des trames capturées demanderait un modèle esclave dédié.
- **Aucun maître Modbus.** Les trames émises sur USART1 sont visibles dans
  l'analyseur, mais rien ne répond côté hôte.
- **Temporisations approchées.** La pile Modbus (TIM3) tourne à une fréquence
  approchée et TIM1 reste sur le modèle intégré, sans canal de comparaison : les
  mesures de temps fines ne sont pas représentatives du matériel. TIM4 et TIM8,
  eux, comptent à la période programmée par `PWM_init` (2 MHz, ARR + 1 = 10000,
  soit 200 Hz).
- **Sorties PWM non reliées aux broches.** Renode ne relie pas les fonctions
  alternées des GPIO aux timers : `GetDutyCycle` est la seule observation du
  signal, et PC6, PC7 et PD15 restent à zéro côté `gpioPortC`/`gpioPortD`.
- **RCC, RTC et IWDG fonctionnels, pas fidèles au cycle près** — les bits d'état
  suivent immédiatement les commandes, sans temps de stabilisation.
- **Programmation flash non filtrée.** Le firmware écrit directement dans la
  mémoire ; la règle matérielle « un bit ne passe que de 1 à 0 sans effacement »
  n'est pas appliquée. Sans effet ici, `sov_eeflash` effaçant toujours la page
  avant de la réécrire, mais un firmware qui l'oublierait passerait au travers.
- Quelques avertissements subsistent au démarrage sur des bits non modélisés
  (SPI2 `CR2.DS`, RTC `CR.TSE`, priorité NVIC de l'IRQ 16) : aucun n'affecte
  l'exécution.

### Défauts du firmware mis au jour par ce modèle

La chaîne PWM du firmware se pilote elle-même : `TIM8_CC_IRQHandler` et
`TIM4_IRQHandler` sont les seuls appelants de `PWM_write()`, donc les seuls
écrivains de `CCR`, et ils ne s'exécutent que sur une correspondance de
comparaison. Toute valeur de `CCR` strictement supérieure à `ARR` interrompt
définitivement ce bouclage : le compteur ne l'atteint jamais, `CCxIF` ne se lève
plus, l'ISR ne s'exécute plus, et `OCxREF` reste maintenu à 1 (RM0316 §22.3.10),
c'est-à-dire **la sortie bloquée à 100 %**. `PWM_init` prend deux fois ce piège.

**À l'initialisation.** `tim_ocinit_t.TIM_Pulse = 0xFFFF` alors que `ARR` vaut
9999 : le PWM ne démarre jamais. Dès la mise sous tension, le SSR de l'élément
chauffant, la soufflante SBX et la recopie 0-10 V sont commandés à 100 %.

**À pleine puissance.** `PWM_write` écrit `CCR = PWM_FACTOR × valeur`, soit
`100 × 100 = 10000` pour 100 %, une unité au-dessus de `ARR = 9999`. Or
`state_update` force `output_ssr = 100` à chaque entrée en `STEAM_ON_STATE` tant
que l'eau est sous 95 °C. La toute première commande de pleine puissance tue donc
le bouclage, définitivement : la régulation continue de calculer correctement,
mais plus rien n'atteint la sortie.

Ce n'est pas un artefact d'émulation, le modèle reproduisant ici le comportement
documenté du silicium. Le scénario se rejoue dans la console : machine armée,
demande à 50 %, eau portée à 98 °C, puis demande ramenée à 20 %.

| | `output_ssr` | `TIM8_CCR1` | broche PC6 |
|---|---|---|---|
| démarrage | 0 % | 65535 | 100 % |
| entrée en production, eau froide | 100 % | 10000 | 100 % |
| eau à 98 °C, demande 50 % | 49,99 % | 10000 | 100 % |
| demande 20 % | 20,01 % | 10000 | 100 % |

Côté firmware, deux corrections d'une ligne lèvent les deux verrous : amorcer
`TIM_Pulse = 0` — la correspondance à `CNT = 0` a lieu à chaque période et le
PWM démarre à rapport cyclique nul, qui est aussi l'état sûr — et porter
`pwm_period` à 10001 pour que `ARR = 10000` accueille la pleine échelle de
`PWM_FACTOR`. Les deux se vérifient sans recompiler le firmware, en corrigeant
`ARR` puis en provoquant une correspondance depuis le moniteur :

```
(machine) sysbus WriteDoubleWord 0x4001342C 10000   # TIM8_ARR
(machine) sysbus WriteDoubleWord 0x40013434 5000    # TIM8_CCR1, amorçage
```

Le même scénario donne alors 100 %, 49 %, 100 % puis 20 % sur PC6, la broche
suivant `output_ssr` comme sur une machine réelle. `tests/ier-pwm.robot`
verrouille les deux moitiés du diagnostic : qu'une valeur au-delà de `ARR` ne
produit aucune correspondance, et qu'une seule correspondance suffit à relancer
l'ISR.

## Organisation

```
platforms/ier_stm32f303.repl   description de la carte et du SoC
peripherals/*.cs               modèles compilés à la volée par Renode
scripts/ier.resc               script principal
scripts/sensors.resc           stimuli capteurs et entrées TOR
scripts/eeprom.resc            image de l'EEPROM et macros associées
scripts/ier-debug.resc         idem + serveur GDB
tests/ier-boot.robot           non-régression du démarrage
tests/ier-eeprom.robot         non-régression de l'EEPROM
tests/ier-pwm.robot            non-régression de la chaîne PWM (TIM4, TIM8)
tests/firmware_symbols.py      adresses du firmware pour les tests
tools/install-renode.sh        installation locale de Renode (version épinglée)
tools/mkeeprom.py              générateur d'image EEPROM
var/eeprom.bin                 état de la carte, hors dépôt
run.sh / test.sh               lanceurs

console/firmware.py            carte des E/S et vecteurs d'état, relevés dans ../ier
console/symbols.py             résolution des adresses dans le DWARF de l'ELF
console/monitor.py             client TCP du Monitor Renode, lectures groupées
console/session.py             plan de lecture et construction des commandes
console/worker.py              sondage hors du fil de l'interface
console/widgets.py             composants dessinés d'après le design system
console/window.py              fenêtre principale
console/theme.py               jetons de style steamOvap
console/selfcheck.py           cohérence console / sensors.resc / ELF / EEPROM
console.sh                     lanceur de la console
```

La version de Renode est épinglée dans `tools/install-renode.sh`. C'est une
nightly : la 1.16.1 stable ne fait pas implémenter `IDMA` à `DMA.STM32LDMA`, ce
qui empêche l'ADC de déclencher les transferts DMA.
