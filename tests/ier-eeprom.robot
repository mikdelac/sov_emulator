*** Settings ***
Documentation       Non-régression de l'EEPROM émulée du firmware IER.
...
...                 Le firmware range ses paramètres dans les onze dernières
...                 pages de la flash (sov_eeflash.c). Une page vierge se
...                 reconnaît à son en-tête 0xFFFF, une page valide à 0x0000 —
...                 convention qui suppose une flash effacée, alors qu'une
...                 mémoire Renode démarre à zéro. Ces cas vérifient que la
...                 zone part bien à 0xFF, que le firmware y écrit, et que ce
...                 qu'il a écrit survit à l'arrêt de Renode.
...
...                 Lancement :  ./.renode/renode-test tests/ier-eeprom.robot

Library             Process
Library             OperatingSystem
Library             ${CURDIR}/firmware_symbols.py

Suite Setup         Setup
Suite Teardown      Teardown
Test Teardown       Test Teardown
Resource            ${RENODEKEYWORDS}

*** Variables ***
${ROOT}             ${CURDIR}/..
${SCRIPT}           ${ROOT}/scripts/ier.resc
${ELF}              ${ROOT}/../ier/Debug/ier.elf
${MKEEPROM}         ${ROOT}/tools/mkeeprom.py
${IMAGE}            ${TEMPDIR}/ier-eeprom-test.bin

# Plan mémoire de sov_eeflash.h. Les pages 0 à 9 tournent pour les paramètres,
# la 10 porte les informations d'usine.
${EEPROM_BASE}      ${0x08030000}
${PAGE_SIZE}        ${0x800}
${FACTORY_PAGE}     ${10}
${ERASED}           ${0xFFFF}
${VALID}            ${0x0000}

*** Keywords ***
Build Image
    [Documentation]    Génère une image d'EEPROM avec tools/mkeeprom.py.
    [Arguments]        ${mode}
    Remove File        ${IMAGE}
    ${result}=         Run Process    python3    ${MKEEPROM}    ${mode}    ${IMAGE}
    Should Be Equal As Integers       ${result.rc}    0
    ...                mkeeprom a échoué : ${result.stderr}

Prepare Machine
    [Documentation]    Démarre la machine sur l'image de test plutôt que sur
    ...                l'image de travail de var/, pour que la suite ne dépende
    ...                pas de l'état laissé par une session interactive.
    Execute Command    $eeprom=@${IMAGE}
    Execute Script     ${SCRIPT}

Page Header
    [Arguments]        ${page}
    ${address}=        Evaluate    ${EEPROM_BASE} + ${page} * ${PAGE_SIZE}
    ${value}=          Execute Command    sysbus ReadWord ${address}
    ${value}=          Convert To Integer    ${value.strip()}    16
    RETURN             ${value}

Valid Data Pages
    [Documentation]    Numéros des pages de paramètres portant un en-tête valide.
    ${pages}=          Create List
    FOR    ${page}    IN RANGE    ${FACTORY_PAGE}
        ${header}=     Page Header    ${page}
        IF    ${header} == ${VALID}    Append To List    ${pages}    ${page}
    END
    RETURN             ${pages}

Read Field
    [Documentation]    Lit un champ du firmware à l'adresse résolue dans le
    ...                DWARF — aucun offset de structure n'est écrit ici.
    [Arguments]        ${expression}
    ${address}=        Symbol Address    ${ELF}    ${expression}
    ${width}=          Symbol Width      ${ELF}    ${expression}
    ${value}=          Execute Command    sysbus ReadDoubleWord ${address}
    ${value}=          Convert To Integer    ${value.strip()}    16
    ${value}=          Evaluate    ${value} & ((1 << (8 * ${width})) - 1)
    RETURN             ${value}

*** Test Cases ***
Une EEPROM vierge se présente comme une flash effacée
    Build Image             --blank
    Prepare Machine

    FOR    ${page}    IN RANGE    ${FACTORY_PAGE} + 1
        ${header}=          Page Header    ${page}
        Should Be Equal As Integers    ${header}    ${ERASED}
        ...                 page ${page} à 0x${header} au lieu d'une page effacée
    END

Le firmware écrit ses valeurs par défaut dans une page vierge
    Build Image             --blank
    Prepare Machine
    Execute Command         emulation RunFor "20"

    # Faute de page valide, le firmware appelle fetch_default_value puis
    # demande la sauvegarde : une page de paramètres, et une seule, devient
    # valide. La page d'usine, elle, ne s'écrit pas toute seule.
    ${pages}=               Valid Data Pages
    Length Should Be        ${pages}    1
    ...                     pages valides après démarrage : ${pages}
    ${factory}=             Page Header    ${FACTORY_PAGE}
    Should Be Equal As Integers    ${factory}    ${ERASED}

    # Le symptôme qui a motivé tout ceci : avec une zone lue à zéro, le
    # firmware chargeait ier_service_delay à 0 et levait SERVICE_AL (bit 10)
    # dès le premier tour de la tâche de monitoring.
    ${alarm2}=              Read Field    ier_core.alarm2
    ${service}=             Evaluate    ${alarm2} & ${0b0000010000000000}
    Should Be Equal As Integers    ${service}    0
    ...                     SERVICE_AL levée alors que l'EEPROM est vierge

Sans informations d'usine le firmware reste en FACTORY_STATE
    Build Image             --blank
    Prepare Machine
    Execute Command         emulation RunFor "20"

    ${state}=               Read Field    ier_core.ier_state
    Should Be Equal As Integers    ${state}    9
    ...                     état ${state} au lieu de FACTORY_STATE

L'image d'usine sort le firmware de FACTORY_STATE
    Build Image             --factory
    Prepare Machine
    ${factory}=             Page Header    ${FACTORY_PAGE}
    Should Be Equal As Integers    ${factory}    ${VALID}

    Execute Command         emulation RunFor "20"
    ${state}=               Read Field    ier_core.ier_state
    Should Not Be Equal As Integers    ${state}    9
    ...                     le firmware n'a pas lu ses informations d'usine

    # Entrées ouvertes, filtres pleins : les trois contacts configurés en
    # normalement fermé lèvent leur alarme, et aucune alarme de niveau 2.
    ${alarm2}=              Read Field    ier_core.alarm2
    Should Be Equal As Integers    ${alarm2}    0

Ce que le firmware écrit survit à l'arrêt de Renode
    Build Image             --blank
    Prepare Machine
    Execute Command         emulation RunFor "20"
    ${pages}=               Valid Data Pages
    Length Should Be        ${pages}    1

    # Le contrôleur flash émulé écrit l'image dès que le firmware reverrouille
    # la flash : le fichier doit déjà porter la page, sans rien demander.
    ${size}=                Get File Size    ${IMAGE}
    ${expected}=            Evaluate    (${FACTORY_PAGE} + 1) * ${PAGE_SIZE}
    Should Be Equal As Integers    ${size}    ${expected}

    Execute Command         Clear
    Prepare Machine
    ${reloaded}=            Valid Data Pages
    Should Be Equal         ${reloaded}    ${pages}
    ...                     page perdue au redémarrage : ${reloaded} au lieu de ${pages}
