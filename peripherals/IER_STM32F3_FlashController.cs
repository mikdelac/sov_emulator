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
// Il porte aussi l'EEPROM émulée du firmware (sov_eeflash.c, onze pages à
// partir de 0x08030000). Deux raisons de la mettre ici plutôt que dans un
// script :
//
//   - Une MappedMemory Renode s'initialise à 0x00, une flash STM32 effacée lit
//     0xFF. Or eeflash_getpagestatus compare l'en-tête de page à VALID_PAGE,
//     qui vaut justement 0x0000 : sans remise à 0xFF, le firmware prend une
//     page vierge pour une page valide et charge une configuration nulle —
//     entre autres ier_service_delay à 0, ce qui lève SERVICE_AL au premier
//     tour de la tâche de monitoring.
//   - Le firmware ne peut pas écrire en flash sans passer par ce contrôleur :
//     FLASH_Unlock, FLASH_ErasePage, programmation, puis FLASH_Lock. Le
//     verrouillage qui suit un effacement est donc le moment exact où une page
//     vient d'être réécrite, et le seul endroit où la persistance peut être
//     déclenchée sans rien sonder.
//
using System;
using System.IO;
using Antmicro.Renode.Core;
using Antmicro.Renode.Core.Structure.Registers;
using Antmicro.Renode.Exceptions;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.Memory;

namespace Antmicro.Renode.Peripherals.MTD
{
    public class IER_STM32F3_FlashController : BasicDoubleWordPeripheral, IKnownSize
    {
        public IER_STM32F3_FlashController(IMachine machine, MappedMemory flash,
                                           ulong flashBaseAddress = 0x08000000, uint pageSize = 0x800,
                                           ulong eepromOffset = 0x30000, uint eepromSize = 0x5800) : base(machine)
        {
            this.flash = flash;
            this.flashBaseAddress = flashBaseAddress;
            this.pageSize = pageSize;
            this.eepromOffset = eepromOffset;
            this.eepromSize = eepromSize;
            // L'effacement travaille par pages entières : une zone qui n'en
            // contient pas un nombre entier déborderait sur le firmware, et
            // silencieusement.
            if(eepromSize % pageSize != 0)
            {
                throw new ConstructionException(
                    $"eepromSize (0x{eepromSize:X}) n'est pas un multiple de "
                    + $"pageSize (0x{pageSize:X})");
            }
            if(eepromOffset + eepromSize > (ulong)flash.Size)
            {
                throw new ConstructionException(
                    $"la zone EEPROM déborde de la flash de 0x{flash.Size:X} octets");
            }
            erasedPage = new byte[pageSize];
            for(var i = 0; i < erasedPage.Length; i++)
            {
                erasedPage[i] = 0xFF;
            }

            DefineRegisters();
            Reset();
        }

        public long Size => 0x400;

        // Image de l'EEPROM sur le disque hôte. Positionnée depuis
        // scripts/eeprom.resc ; laissée vide, l'EEPROM ne survit pas à la
        // session, ce qui reste un fonctionnement valable.
        public string PersistenceFile { get; set; }

        public override void Reset()
        {
            base.Reset();
            // L'ordre compte : la zone est d'abord ramenée à l'état d'une flash
            // effacée, puis l'image la recouvre. Une image absente ou partielle
            // laisse donc des pages vierges plutôt que des pages nulles.
            EraseEeprom();
            LoadEeprom();
        }

        // -- EEPROM émulée -----------------------------------------------------

        // Ramène les onze pages à l'état sortie d'usine (0xFF), sans toucher au
        // firmware chargé plus bas dans la flash.
        public void EraseEeprom()
        {
            for(ulong offset = 0; offset < eepromSize; offset += pageSize)
            {
                flash.WriteBytes((long)(eepromOffset + offset), erasedPage);
            }
            dirty = false;
        }

        public void LoadEeprom()
        {
            if(string.IsNullOrEmpty(PersistenceFile) || !File.Exists(PersistenceFile))
            {
                return;
            }

            byte[] image;
            try
            {
                image = File.ReadAllBytes(PersistenceFile);
            }
            catch(IOException exception)
            {
                this.Log(LogLevel.Warning, "Image EEPROM illisible ({0}) : {1}",
                         PersistenceFile, exception.Message);
                return;
            }

            if(image.Length != (int)eepromSize)
            {
                this.Log(LogLevel.Warning,
                         "Image EEPROM de {0} octets au lieu de {1} : {2}",
                         image.Length, eepromSize, PersistenceFile);
            }

            var length = Math.Min(image.Length, (int)eepromSize);
            for(var written = 0; written < length; written += (int)pageSize)
            {
                var chunk = new byte[Math.Min((int)pageSize, length - written)];
                Array.Copy(image, written, chunk, 0, chunk.Length);
                flash.WriteBytes((long)eepromOffset + written, chunk);
            }
            dirty = false;
            this.Log(LogLevel.Debug, "EEPROM chargée depuis {0}", PersistenceFile);
        }

        public void SaveEeprom()
        {
            if(string.IsNullOrEmpty(PersistenceFile))
            {
                throw new RecoverableException(
                    "Aucune image EEPROM configurée — voir scripts/eeprom.resc");
            }

            var image = new byte[eepromSize];
            // Lecture page par page : ReadBytes ne garantit rien au-delà d'un
            // segment de la MappedMemory, et une page est l'unité du firmware.
            for(var read = 0; read < (int)eepromSize; read += (int)pageSize)
            {
                var count = Math.Min((int)pageSize, (int)eepromSize - read);
                flash.ReadBytes((long)eepromOffset + read, count, image, read);
            }

            try
            {
                var directory = Path.GetDirectoryName(PersistenceFile);
                if(!string.IsNullOrEmpty(directory))
                {
                    Directory.CreateDirectory(directory);
                }
                // Écriture en deux temps : Renode tué en cours de route laisse
                // l'image précédente intacte plutôt qu'un fichier tronqué.
                var temporary = PersistenceFile + ".tmp";
                File.WriteAllBytes(temporary, image);
                File.Move(temporary, PersistenceFile, true);
            }
            catch(IOException exception)
            {
                this.Log(LogLevel.Warning, "Image EEPROM non écrite ({0}) : {1}",
                         PersistenceFile, exception.Message);
                return;
            }
            dirty = false;
            this.Log(LogLevel.Debug, "EEPROM écrite dans {0}", PersistenceFile);
        }

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
                .WithFlag(7, out locked, name: "LOCK",
                    // FLASH_Lock() ferme la séquence d'écriture du firmware :
                    // c'est le seul instant où l'on sait qu'une page vient
                    // d'être effacée puis reprogrammée en entier.
                    writeCallback: (previous, value) =>
                    {
                        if(value && !previous && dirty)
                        {
                            SaveEepromIfConfigured();
                        }
                    })
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
                dirty = true;
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

            var pageOffset = pageStart - flashBaseAddress;
            flash.WriteBytes((long)pageOffset, erasedPage);
            endOfOperation.Value = true;
            if(pageOffset >= eepromOffset && pageOffset < eepromOffset + eepromSize)
            {
                dirty = true;
            }
            this.Log(LogLevel.Debug, "Page effacée à 0x{0:X}", pageStart);
        }

        // La persistance est facultative : sans image configurée, une écriture
        // du firmware ne doit pas interrompre l'émulation.
        private void SaveEepromIfConfigured()
        {
            if(string.IsNullOrEmpty(PersistenceFile))
            {
                dirty = false;
                return;
            }
            SaveEeprom();
        }

        private readonly MappedMemory flash;
        private readonly ulong flashBaseAddress;
        private readonly uint pageSize;
        private readonly ulong eepromOffset;
        private readonly uint eepromSize;
        private readonly byte[] erasedPage;

        private bool firstKeyWritten;
        private bool dirty;

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
