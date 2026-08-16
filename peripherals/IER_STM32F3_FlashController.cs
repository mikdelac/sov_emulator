//
// Interface flash pour STM32F3 (RM0316 §4.5), écrite pour l'émulation du firmware IER.
//
// Renode ne fournit pas de contrôleur F3. Le modèle F4 partage les offsets de
// registres mais pas la sémantique : sur F4 le bit LOCK est en position 31,
// sur F3 en position 7. Le firmware relit CR puis le réécrit, ce qui repositionne
// aussitôt le bit 31 côté modèle F4 : la flash se reverrouille et les 95
// écritures de sov_eeflash au démarrage (fetch_default_value) échouent toutes,
// laissant les paramètres de régulation à zéro.
//
// Ce modèle implémente le déverrouillage par clés, l'effacement de page (2 Ko
// sur STM32F303xC) et les drapeaux d'état attendus par FLASH_WaitForLastOperation.
// La programmation elle-même n'a rien à intercepter : le firmware écrit
// directement dans la mémoire flash, que Renode rend inscriptible.
//
using Antmicro.Renode.Core;
using Antmicro.Renode.Core.Structure.Registers;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.Memory;

namespace Antmicro.Renode.Peripherals.MTD
{
    public class IER_STM32F3_FlashController : BasicDoubleWordPeripheral, IKnownSize
    {
        public IER_STM32F3_FlashController(IMachine machine, MappedMemory flash,
                                           ulong flashBaseAddress = 0x08000000, uint pageSize = 0x800) : base(machine)
        {
            this.flash = flash;
            this.flashBaseAddress = flashBaseAddress;
            this.pageSize = pageSize;
            erasedPage = new byte[pageSize];
            for(var i = 0; i < erasedPage.Length; i++)
            {
                erasedPage[i] = 0xFF;
            }

            DefineRegisters();
            Reset();
        }

        public long Size => 0x400;

        private void DefineRegisters()
        {
            Registers.AccessControl.Define(this, 0x00000030)
                .WithValueField(0, 3, name: "LATENCY")
                .WithFlag(3, name: "HLFCYA")
                .WithFlag(4, out prefetchEnabled, name: "PRFTBE")
                .WithFlag(5, FieldMode.Read, name: "PRFTBS",
                    valueProviderCallback: _ => prefetchEnabled.Value)
                .WithReservedBits(6, 26)
            ;

            Registers.Key.Define(this)
                .WithValueField(0, 32, FieldMode.Write, name: "FKEYR",
                    writeCallback: (_, value) =>
                    {
                        if(value == FlashKey1)
                        {
                            firstKeyWritten = true;
                        }
                        else if(value == FlashKey2 && firstKeyWritten)
                        {
                            locked.Value = false;
                            firstKeyWritten = false;
                        }
                        else
                        {
                            firstKeyWritten = false;
                        }
                    })
            ;

            Registers.OptionKey.Define(this)
                .WithValueField(0, 32, FieldMode.Write, name: "OPTKEYR");

            Registers.Status.Define(this)
                .WithFlag(0, FieldMode.Read, name: "BSY",       // effacement immédiat : jamais occupé
                    valueProviderCallback: _ => false)
                .WithReservedBits(1, 1)
                .WithFlag(2, out programmingError, FieldMode.Read | FieldMode.WriteOneToClear, name: "PGERR")
                .WithReservedBits(3, 1)
                .WithFlag(4, out writeProtectionError, FieldMode.Read | FieldMode.WriteOneToClear, name: "WRPRTERR")
                .WithFlag(5, out endOfOperation, FieldMode.Read | FieldMode.WriteOneToClear, name: "EOP")
                .WithReservedBits(6, 26)
            ;

            Registers.Control.Define(this, 0x00000080)
                .WithFlag(0, out programming, name: "PG")
                .WithFlag(1, out pageErase, name: "PER")
                .WithFlag(2, out massErase, name: "MER")
                .WithReservedBits(3, 1)
                .WithFlag(4, name: "OPTPG")
                .WithFlag(5, name: "OPTER")
                .WithFlag(6, FieldMode.Write, name: "STRT",
                    writeCallback: (_, value) =>
                    {
                        if(value)
                        {
                            PerformErase();
                        }
                    })
                .WithFlag(7, out locked, name: "LOCK")
                .WithReservedBits(8, 1)
                .WithFlag(9, name: "OPTWRE")
                .WithFlag(10, name: "ERRIE")
                .WithReservedBits(11, 1)
                .WithFlag(12, name: "EOPIE")
                .WithFlag(13, name: "FORCE_OPTLOAD")
                .WithReservedBits(14, 18)
            ;

            Registers.Address.Define(this)
                .WithValueField(0, 32, out address, FieldMode.Write, name: "AR");

            Registers.OptionByte.Define(this, 0x03FFFFF2)
                .WithValueField(0, 32, FieldMode.Read, name: "OBR");

            Registers.WriteProtection.Define(this, 0xFFFFFFFF)
                .WithValueField(0, 32, FieldMode.Read, name: "WRPR");
        }

        private void PerformErase()
        {
            if(locked.Value)
            {
                this.Log(LogLevel.Warning, "Effacement demandé alors que la flash est verrouillée");
                writeProtectionError.Value = true;
                return;
            }

            if(massErase.Value)
            {
                for(ulong offset = 0; offset < (ulong)flash.Size; offset += pageSize)
                {
                    flash.WriteBytes((long)offset, erasedPage);
                }
                endOfOperation.Value = true;
                return;
            }

            if(!pageErase.Value)
            {
                return;
            }

            var pageStart = address.Value & ~((ulong)pageSize - 1);
            if(pageStart < flashBaseAddress || pageStart + pageSize > flashBaseAddress + (ulong)flash.Size)
            {
                this.Log(LogLevel.Warning, "Effacement hors de la flash demandé à 0x{0:X}", address.Value);
                programmingError.Value = true;
                return;
            }

            flash.WriteBytes((long)(pageStart - flashBaseAddress), erasedPage);
            endOfOperation.Value = true;
            this.Log(LogLevel.Debug, "Page effacée à 0x{0:X}", pageStart);
        }

        private readonly MappedMemory flash;
        private readonly ulong flashBaseAddress;
        private readonly uint pageSize;
        private readonly byte[] erasedPage;

        private bool firstKeyWritten;

        private IFlagRegisterField prefetchEnabled;
        private IFlagRegisterField locked;
        private IFlagRegisterField programming;
        private IFlagRegisterField pageErase;
        private IFlagRegisterField massErase;
        private IFlagRegisterField endOfOperation;
        private IFlagRegisterField programmingError;
        private IFlagRegisterField writeProtectionError;
        private IValueRegisterField address;

        private const uint FlashKey1 = 0x45670123;
        private const uint FlashKey2 = 0xCDEF89AB;

        private enum Registers
        {
            AccessControl = 0x00,
            Key = 0x04,
            OptionKey = 0x08,
            Status = 0x0C,
            Control = 0x10,
            Address = 0x14,
            OptionByte = 0x1C,
            WriteProtection = 0x20,
        }
    }
}
