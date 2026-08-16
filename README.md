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
```

La version de Renode est épinglée dans `tools/install-renode.sh`. C'est une
nightly : la 1.16.1 stable ne fait pas implémenter `IDMA` à `DMA.STM32LDMA`, ce
qui empêche l'ADC de déclencher les transferts DMA.
