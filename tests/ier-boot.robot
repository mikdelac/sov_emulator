*** Settings ***
Documentation       Non-régression de l'émulation du firmware IER sur STM32F303xC.
...                 Lancement :  ./.renode/renode-test tests/ier-boot.robot

Suite Setup         Setup
Suite Teardown      Teardown
Test Teardown       Test Teardown
Resource            ${RENODEKEYWORDS}

*** Variables ***
${SCRIPT}           ${CURDIR}/../scripts/ier.resc

*** Keywords ***
Prepare Machine
    Execute Script          ${SCRIPT}
    Execute Command         machine StartGdbServer 3335 false

Read Symbol
    [Arguments]             ${symbol}    ${offset}=0
    ${base}=                Execute Command    sysbus GetSymbolAddress "${symbol}"
    ${address}=             Convert To Integer    ${base.strip()}    16
    ${address}=             Evaluate    ${address} + ${offset}
    ${value}=               Execute Command    sysbus ReadDoubleWord ${address}
    ${value}=               Convert To Integer    ${value.strip()}    16
    RETURN                  ${value}

*** Test Cases ***
L'ordonnanceur FreeRTOS démarre et le tick avance
    Prepare Machine
    Execute Command         emulation RunFor "3"
    ${first}=               Read Symbol    xTickCount
    Should Be True          ${first} > 250      Le tick FreeRTOS n'avance pas (${first} après 3 s)

    Execute Command         emulation RunFor "3"
    ${second}=              Read Symbol    xTickCount
    Should Be True          ${second} > ${first}    Le tick s'est figé à ${first}

La chaîne ADC vers DMA transporte les tensions injectées
    Prepare Machine
    Execute Command         emulation RunFor "3"

    # adc1_dma_value est un uint16_t[9] rempli par DMA1_Channel1 dans l'ordre de
    # la séquence : wl, ad, rh, vrefint, wt, vrefint, hl, et, uctemp.
    ${words01}=             Read Symbol    adc1_dma_value    0
    ${wl}=                  Evaluate    ${words01} & 0xFFFF
    ${ad}=                  Evaluate    (${words01} >> 16) & 0xFFFF
    ${words23}=             Read Symbol    adc1_dma_value    4
    ${rh}=                  Evaluate    ${words23} & 0xFFFF
    ${vref}=                Evaluate    (${words23} >> 16) & 0xFFFF

    # Valeurs par défaut de scripts/sensors.resc, converties sur 12 bits / 3,3 V
    Should Be Equal As Integers    ${wl}      1117      wl attendu à 0,90 V
    Should Be Equal As Integers    ${ad}      0         ad attendu à 0 V
    Should Be Equal As Integers    ${rh}      1861      rh attendu à 1,50 V
    Should Be Equal As Integers    ${vref}    1526      Vrefint attendu à 1,23 V

Un changement de tension se propage jusqu'au firmware
    Prepare Machine
    Execute Command         emulation RunFor "3"
    Execute Command         sysbus.adc1 SetVoltage 2 3.0
    Execute Command         emulation RunFor "1"

    ${words01}=             Read Symbol    adc1_dma_value    0
    ${ad}=                  Evaluate    (${words01} >> 16) & 0xFFFF
    Should Be Equal As Integers    ${ad}    3723    la demande à 3,0 V n'a pas été prise en compte

La tâche de lecture des capteurs alimente le buffer circulaire
    Prepare Machine
    Execute Command         emulation RunFor "3"
    ${idx}=                 Read Symbol    ier_input    0
    Should Be True          ${idx} > 0    ier_input.idx est resté à zéro

Le firmware survit au chien de garde sur la durée
    Prepare Machine
    Execute Command         emulation RunFor "25"
    ${tick}=                Read Symbol    xTickCount
    # Un reset du watchdog remettrait le compteur près de zéro.
    Should Be True          ${tick} > 2000    Redémarrage suspecté (tick = ${tick})
