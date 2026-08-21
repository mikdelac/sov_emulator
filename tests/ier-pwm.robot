*** Settings ***
Documentation       Non-régression de la chaîne PWM du firmware IER (TIM8 et TIM4).
...                 Le modèle intégré Timers.STM32_Timer ne modélise aucun canal
...                 de comparaison : CCR ne mémorise rien, CCxIF n'est jamais levé
...                 et les ISR qui pilotent le SSR ne s'exécutent jamais. Ces cas
...                 verrouillent le comportement du modèle IER_STM32_TimerPWM.
...                 Lancement :  ./.renode/renode-test tests/ier-pwm.robot

Suite Setup         Setup
Suite Teardown      Teardown
Test Teardown       Test Teardown
Resource            ${RENODEKEYWORDS}

*** Variables ***
${SCRIPT}           ${CURDIR}/../scripts/ier.resc

# TIM8 (avancé, APB2) — SSR élément chauffant PC6 et soufflante SBX PC7
${TIM8}             ${0x40013400}
# TIM4 (général, APB1) — recopie 0-10 V PD15
${TIM4}             ${0x40000800}

${SR}               ${0x10}
${CCER}             ${0x20}
${CNT}              ${0x24}
${PSC}              ${0x28}
${ARR}              ${0x2C}
${CCR1}             ${0x34}
${CCR2}             ${0x38}
${CCR3}             ${0x3C}
${CCR4}             ${0x40}
${BDTR}             ${0x44}

# Attention : Robot Framework ne distingue pas la casse des noms de variables.
# Les valeurs relues portent donc des noms distincts des constantes ci-dessus,
# sans quoi ${sr} écraserait silencieusement l'offset ${SR}.

*** Keywords ***
Prepare Machine
    Execute Script          ${SCRIPT}
    # PWM_init s'exécute au démarrage de task_monitor ; deux secondes suffisent
    # largement pour que la base de temps soit programmée.
    Execute Command         emulation RunFor "2"

Read Register
    [Arguments]             ${base}    ${offset}
    ${value}=               Execute Command    sysbus ReadDoubleWord ${${base} + ${offset}}
    ${value}=               Convert To Integer    ${value.strip()}    16
    RETURN                  ${value}

Write Register
    [Arguments]             ${base}    ${offset}    ${value}
    Execute Command         sysbus WriteDoubleWord ${${base} + ${offset}} ${value}

*** Test Cases ***
La base de temps PWM est programmée et le compteur avance
    Prepare Machine
    # PWM_init : 72 MHz / (35 + 1) = 2 MHz, ARR + 1 = 10000 -> 200 Hz
    ${diviseur}=                 Read Register    TIM8    ${PSC}
    ${periode}=                 Read Register    TIM8    ${ARR}
    Should Be Equal As Integers    ${diviseur}    35      prescaler TIM8 inattendu
    Should Be Equal As Integers    ${periode}    9999    ARR TIM8 inattendu

    ${first}=               Read Register    TIM8    ${CNT}
    Should Be True          ${first} <= ${periode}    CNT déborde de ARR (${first})
    Execute Command         emulation RunFor "0.001"
    ${second}=              Read Register    TIM8    ${CNT}
    Should Not Be Equal As Integers    ${first}    ${second}    CNT est figé à ${first}

Les registres de comparaison mémorisent la valeur écrite
    Prepare Machine
    # Le modèle intégré renvoyait 0 quoi qu'on écrive : le rapport cyclique
    # affiché par la console était donc toujours faux.
    Write Register          TIM8    ${CCR3}    4242
    ${voie3}=                Read Register    TIM8    ${CCR3}
    Should Be Equal As Integers    ${voie3}    4242    CCR3 de TIM8 ne mémorise rien

    Write Register          TIM4    ${CCR2}    1234
    ${voie2}=                Read Register    TIM4    ${CCR2}
    Should Be Equal As Integers    ${voie2}    1234    CCR2 de TIM4 ne mémorise rien

Une correspondance lève le drapeau CCxIF de la voie
    Prepare Machine
    # CC3IE est éteint sur TIM8 : le drapeau doit se lever sans interruption.
    Write Register          TIM8    ${SR}      0
    Write Register          TIM8    ${CCR3}    1000
    Execute Command         emulation RunFor "0.02"
    ${etat}=                  Read Register    TIM8    ${SR}
    ${cc3if}=               Evaluate    (${etat} >> 3) & 1
    Should Be Equal As Integers    ${cc3if}    1    CC3IF ne se lève pas (SR = ${etat})

Une valeur de comparaison au-delà de ARR ne produit aucune correspondance
    Prepare Machine
    # Comportement documenté : CCRx > ARR maintient OCxREF à 1 sans jamais
    # produire d'événement. C'est ce que fait PWM_init avec TIM_Pulse = 0xFFFF.
    ${voie1}=                Read Register    TIM8    ${CCR1}
    Should Be Equal As Integers    ${voie1}    65535    CCR1 n'a pas la valeur d'amorçage du firmware
    Write Register          TIM8    ${SR}    0
    Execute Command         emulation RunFor "0.05"
    ${etat}=                  Read Register    TIM8    ${SR}
    ${cc1if}=               Evaluate    (${etat} >> 1) & 1
    Should Be Equal As Integers    ${cc1if}    0    CC1IF se lève alors que CCR1 dépasse ARR

L'interruption TIM8_CC atteint le firmware et recharge les deux voies
    Prepare Machine
    # Une fois CCR1 amené sous ARR, la correspondance déclenche
    # TIM8_CC_IRQHandler, seul appelant de PWM_write : le gestionnaire réécrit
    # CCR1 depuis ier_output.pwm_out[0].ssr0 et CCR2 depuis .sbx.
    Write Register          TIM8    ${CCR1}    5000
    Execute Command         emulation RunFor "0.05"
    ${voie1}=                Read Register    TIM8    ${CCR1}
    ${voie2}=                Read Register    TIM8    ${CCR2}
    Should Not Be Equal As Integers    ${voie1}    5000     TIM8_CC_IRQHandler ne s'exécute pas
    Should Be True          ${voie1} <= 10000    CCR1 hors plage après rechargement (${voie1})
    Should Be True          ${voie2} <= 10000    CCR2 hors plage après rechargement (${voie2})

L'interruption TIM4 atteint le firmware et recharge la recopie 0-10 V
    Prepare Machine
    Write Register          TIM4    ${CCR4}    5000
    Execute Command         emulation RunFor "0.05"
    ${voie4}=                Read Register    TIM4    ${CCR4}
    Should Not Be Equal As Integers    ${voie4}    5000    TIM4_IRQHandler ne s'exécute pas
    Should Be True          ${voie4} <= 10000    CCR4 hors plage après rechargement (${voie4})

Le rapport cyclique se relit depuis le moniteur
    Prepare Machine
    # CC2E est armé et MOE validé par PWM_init : la voie est réellement pilotée.
    ${validation}=                Read Register    TIM8    ${CCER}
    ${cc2e}=                Evaluate    (${validation} >> 4) & 1
    Should Be Equal As Integers    ${cc2e}    1    CC2E n'est pas armé sur TIM8
    ${securite}=                Read Register    TIM8    ${BDTR}
    ${moe}=                 Evaluate    (${securite} >> 15) & 1
    Should Be Equal As Integers    ${moe}    1    MOE n'est pas validé sur TIM8

    # CCR1 reste à 0xFFFF : aucune interruption ne vient écraser CCR2.
    Write Register          TIM8    ${CCR2}    2500
    ${duty}=                Execute Command    sysbus.timer8 GetDutyCycle 2
    ${duty}=                Convert To Number    ${duty.strip()}
    Should Be True          24.9 < ${duty} < 25.1    rapport cyclique lu à ${duty} au lieu de 25

Une voie désarmée ne présente aucun rapport cyclique
    Prepare Machine
    # CC1E de TIM4 n'est jamais armé : seule la voie 4 est configurée.
    ${validation}=                Read Register    TIM4    ${CCER}
    ${cc1e}=                Evaluate    ${validation} & 1
    Should Be Equal As Integers    ${cc1e}    0    CC1E de TIM4 est armé alors que le firmware ne l'initialise pas
    ${duty}=                Execute Command    sysbus.timer4 GetDutyCycle 1
    ${duty}=                Convert To Number    ${duty.strip()}
    Should Be Equal As Numbers    ${duty}    0    une voie désarmée annonce un rapport cyclique de ${duty}

La correspondance se perd exactement à ARR + 1
    Prepare Machine
    # PWM_write écrit CCR = PWM_FACTOR × pourcentage, soit 10000 pour 100 %,
    # une unité au-dessus de ARR = 9999. La pleine puissance sort donc de la
    # plage de comparaison et interrompt le bouclage de l'ISR.
    Write Register          TIM8    ${CCR3}    10000
    Write Register          TIM8    ${SR}      0
    Execute Command         emulation RunFor "0.05"
    ${etat}=                  Read Register    TIM8    ${SR}
    ${cc3if}=               Evaluate    (${etat} >> 3) & 1
    Should Be Equal As Integers    ${cc3if}    0    CC3IF se lève avec CCR = ARR + 1 (SR = ${etat})

    Write Register          TIM8    ${CCR3}    9999
    Write Register          TIM8    ${SR}      0
    Execute Command         emulation RunFor "0.05"
    ${etat}=                  Read Register    TIM8    ${SR}
    ${cc3if}=               Evaluate    (${etat} >> 3) & 1
    Should Be Equal As Integers    ${cc3if}    1    CC3IF ne se lève pas avec CCR = ARR (SR = ${etat})
