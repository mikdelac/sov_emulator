//
// Modèle RCC pour STM32F3 (RM0316), écrit pour l'émulation du firmware IER.
//
// Renode ne fournit pas de RCC F3. Le layout de registres du F3 est celui du F0
// (Miscellaneous.STM32F0_RCC), mais ce modèle-là laisse LSIRDY et LSERDY en
// « tagged flags » renvoyant toujours 0, ce qui bloque le firmware IER dans
// hal.c:2771 (attente de RCC_FLAG_LSIRDY) et empêche le démarrage du domaine RTC.
//
// Ici chaque bit xxxRDY suit immédiatement le bit xxxON correspondant, et SWS
// recopie SW : le firmware ne peut donc jamais rester bloqué sur une attente
// d'horloge. Les registres d'activation/reset (AHBENR, APBxENR, ...) sont de
// simples registres lus/écrits, ce qu'attend la lib standard periph.
//
// Ce fichier est chargé par les scripts .resc via « include @... ».
//
using Antmicro.Renode.Core;
using Antmicro.Renode.Core.Structure.Registers;
using Antmicro.Renode.Peripherals.Bus;

namespace Antmicro.Renode.Peripherals.Miscellaneous
{
    public class STM32F3_RCC : BasicDoubleWordPeripheral, IKnownSize
    {
        public STM32F3_RCC(IMachine machine) : base(machine)
        {
            DefineRegisters();
            Reset();
        }

        public long Size => 0x400;

        private void DefineRegisters()
        {
            // CR : HSI actif et prêt au reset (0x83 = HSION | HSIRDY | HSITRIM=16)
            Registers.ClockControl.Define(this, 0x00000083)
                .WithFlag(0, out var hsiEnabled, name: "HSION")
                .WithFlag(1, FieldMode.Read, name: "HSIRDY",
                    valueProviderCallback: _ => hsiEnabled.Value)
                .WithReservedBits(2, 1)
                .WithValueField(3, 5, name: "HSITRIM")
                .WithValueField(8, 8, FieldMode.Read, name: "HSICAL")
                .WithFlag(16, out var hseEnabled, name: "HSEON")
                .WithFlag(17, FieldMode.Read, name: "HSERDY",
                    valueProviderCallback: _ => hseEnabled.Value)
                .WithFlag(18, name: "HSEBYP")
                .WithFlag(19, name: "CSSON")
                .WithReservedBits(20, 4)
                .WithFlag(24, out var pllEnabled, name: "PLLON")
                .WithFlag(25, FieldMode.Read, name: "PLLRDY",
                    valueProviderCallback: _ => pllEnabled.Value)
                .WithReservedBits(26, 6)
            ;

            // CFGR : SWS suit SW, sinon le firmware boucle après avoir basculé sur la PLL
            Registers.ClockConfiguration.Define(this)
                .WithValueField(0, 2, out var systemClockSwitch, name: "SW")
                .WithValueField(2, 2, FieldMode.Read, name: "SWS",
                    valueProviderCallback: _ => systemClockSwitch.Value)
                .WithValueField(4, 4, name: "HPRE")
                .WithValueField(8, 3, name: "PPRE1")
                .WithValueField(11, 3, name: "PPRE2")
                .WithReservedBits(14, 1)
                .WithValueField(15, 2, name: "PLLSRC")
                .WithFlag(17, name: "PLLXTPRE")
                .WithValueField(18, 4, name: "PLLMUL")
                .WithFlag(22, name: "USBPRE")
                .WithFlag(23, name: "I2SSRC")
                .WithValueField(24, 3, name: "MCO")
                .WithReservedBits(27, 1)
                .WithValueField(28, 3, name: "MCOF")
                .WithReservedBits(31, 1)
            ;

            // CIR : les drapeaux d'interruption d'horloge ne sont pas utilisés par l'IER
            Registers.ClockInterrupt.Define(this)
                .WithValueField(0, 32, name: "CIR")
            ;

            Registers.APB2PeripheralReset.Define(this)
                .WithValueField(0, 32, name: "APB2RSTR");
            Registers.APB1PeripheralReset.Define(this)
                .WithValueField(0, 32, name: "APB1RSTR");
            Registers.AHBPeripheralClockEnable.Define(this, 0x00000014)
                .WithValueField(0, 32, name: "AHBENR");
            Registers.APB2PeripheralClockEnable.Define(this)
                .WithValueField(0, 32, name: "APB2ENR");
            Registers.APB1PeripheralClockEnable.Define(this)
                .WithValueField(0, 32, name: "APB1ENR");

            // BDCR : LSERDY suit LSEON (domaine sauvegardé / RTC)
            Registers.RTCDomainControl.Define(this)
                .WithFlag(0, out var lseEnabled, name: "LSEON")
                .WithFlag(1, FieldMode.Read, name: "LSERDY",
                    valueProviderCallback: _ => lseEnabled.Value)
                .WithFlag(2, name: "LSEBYP")
                .WithValueField(3, 2, name: "LSEDRV")
                .WithReservedBits(5, 3)
                .WithValueField(8, 2, name: "RTCSEL")
                .WithReservedBits(10, 5)
                .WithFlag(15, name: "RTCEN")
                .WithFlag(16, name: "BDRST")
                .WithReservedBits(17, 15)
            ;

            // CSR : LSIRDY suit LSION — déblocage de l'attente en hal.c:2771
            Registers.ControlStatus.Define(this, 0x0C000000)
                .WithFlag(0, out var lsiEnabled, name: "LSION")
                .WithFlag(1, FieldMode.Read, name: "LSIRDY",
                    valueProviderCallback: _ => lsiEnabled.Value)
                .WithReservedBits(2, 22)
                .WithFlag(24, FieldMode.Write, name: "RMVF")
                .WithFlag(25, name: "OBLRSTF")
                .WithFlag(26, name: "PINRSTF")
                .WithFlag(27, name: "PORRSTF")
                .WithFlag(28, name: "SFTRSTF")
                .WithFlag(29, name: "IWDGRSTF")
                .WithFlag(30, name: "WWDGRSTF")
                .WithFlag(31, name: "LPWRRSTF")
            ;

            Registers.AHBPeripheralReset.Define(this)
                .WithValueField(0, 32, name: "AHBRSTR");
            Registers.ClockConfiguration2.Define(this)
                .WithValueField(0, 32, name: "CFGR2");
            Registers.ClockConfiguration3.Define(this)
                .WithValueField(0, 32, name: "CFGR3");
        }

        private enum Registers
        {
            ClockControl = 0x00,
            ClockConfiguration = 0x04,
            ClockInterrupt = 0x08,
            APB2PeripheralReset = 0x0C,
            APB1PeripheralReset = 0x10,
            AHBPeripheralClockEnable = 0x14,
            APB2PeripheralClockEnable = 0x18,
            APB1PeripheralClockEnable = 0x1C,
            RTCDomainControl = 0x20,
            ControlStatus = 0x24,
            AHBPeripheralReset = 0x28,
            ClockConfiguration2 = 0x2C,
            ClockConfiguration3 = 0x30,
        }
    }
}
