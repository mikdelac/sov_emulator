//
// Modèle ADC pour STM32F3 (RM0316 §15), écrit pour l'émulation du firmware IER.
//
// Le modèle intégré Analog.STM32F3_ADC ne définit que le registre SQR1, soit
// quatre conversions régulières au maximum et des champs SQx de 4 bits. Le
// firmware IER programme une séquence de NEUF conversions (ADC_init,
// ../ier/src/hal.c) incluant les canaux internes 16 et 18 : le séquenceur
// intégré déréférence alors une entrée nulle et Renode s'arrête sur une
// NullReferenceException.
//
// Ce modèle implémente SQR1 à SQR4 (16 conversions, champs de 5 bits), la
// calibration, le mode continu et la génération de requêtes DMA, ce qui suffit
// à la chaîne ADC1 -> DMA1_Channel1 -> adc1_dma_value du firmware.
//
// Les tensions présentées sur chaque canal se règlent depuis le moniteur :
//     sysbus.adc1 SetVoltage 4 1.56
//     sysbus.adc1 GetVoltage 4
//
using System;

using Antmicro.Renode.Core;
using Antmicro.Renode.Core.Structure.Registers;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.DMA;
using Antmicro.Renode.Peripherals.Timers;
using Antmicro.Renode.Time;

namespace Antmicro.Renode.Peripherals.Analog
{
    [AllowedTranslations(AllowedTranslation.WordToDoubleWord | AllowedTranslation.ByteToDoubleWord)]
    public class IER_STM32F3_ADC : BasicDoubleWordPeripheral, IKnownSize
    {
        public IER_STM32F3_ADC(IMachine machine, double referenceVoltage = 3.3, int dmaChannel = 0,
                               IDMA dmaPeripheral = null, ulong conversionFrequency = 100000) : base(machine)
        {
            this.referenceVoltage = referenceVoltage;
            this.dmaChannel = dmaChannel;
            this.dma = dmaPeripheral;

            IRQ = new GPIO();
            voltages = new decimal[ChannelCount];
            sequence = new IValueRegisterField[MaximumSequenceLength];

            conversionTimer = new LimitTimer(machine.ClockSource, conversionFrequency, this, "conversion",
                limit: 1, direction: Direction.Ascending, enabled: false, eventEnabled: true, autoUpdate: true);
            conversionTimer.LimitReached += PerformConversion;

            DefineRegisters();
            Reset();
        }

        public override void Reset()
        {
            // Les tensions injectées survivent volontairement à un reset : un
            // redémarrage du firmware ne doit pas vider le banc de capteurs.
            conversionTimer.Enabled = false;
            sequenceIndex = 0;
            dataRegister = 0;
            base.Reset();
            IRQ.Unset();
        }

        // Tension présentée sur un canal, en volts.
        public void SetVoltage(int channel, decimal volts)
        {
            if(channel < 0 || channel >= ChannelCount)
            {
                throw new Exceptions.RecoverableException($"Canal {channel} hors plage (0-{ChannelCount - 1})");
            }
            voltages[channel] = volts;
        }

        public decimal GetVoltage(int channel)
        {
            if(channel < 0 || channel >= ChannelCount)
            {
                throw new Exceptions.RecoverableException($"Canal {channel} hors plage (0-{ChannelCount - 1})");
            }
            return voltages[channel];
        }

        public GPIO IRQ { get; }

        public long Size => 0x400;

        private void DefineRegisters()
        {
            Registers.InterruptAndStatus.Define(this)
                .WithFlag(0, out adcReady, FieldMode.Read | FieldMode.WriteOneToClear, name: "ADRDY")
                .WithFlag(1, out endOfSampling, FieldMode.Read | FieldMode.WriteOneToClear, name: "EOSMP")
                .WithFlag(2, out endOfConversion, FieldMode.Read | FieldMode.WriteOneToClear, name: "EOC")
                .WithFlag(3, out endOfSequence, FieldMode.Read | FieldMode.WriteOneToClear, name: "EOS")
                .WithFlag(4, out overrun, FieldMode.Read | FieldMode.WriteOneToClear, name: "OVR")
                .WithFlag(5, FieldMode.Read | FieldMode.WriteOneToClear, name: "JEOC")
                .WithFlag(6, FieldMode.Read | FieldMode.WriteOneToClear, name: "JEOS")
                .WithFlag(7, FieldMode.Read | FieldMode.WriteOneToClear, name: "AWD1")
                .WithFlag(8, FieldMode.Read | FieldMode.WriteOneToClear, name: "AWD2")
                .WithFlag(9, FieldMode.Read | FieldMode.WriteOneToClear, name: "AWD3")
                .WithFlag(10, FieldMode.Read | FieldMode.WriteOneToClear, name: "JQOVF")
                .WithReservedBits(11, 21)
                .WithWriteCallback((_, __) => UpdateInterrupts())
            ;

            Registers.InterruptEnable.Define(this)
                .WithFlag(0, out adcReadyInterruptEnable, name: "ADRDYIE")
                .WithFlag(1, name: "EOSMPIE")
                .WithFlag(2, out endOfConversionInterruptEnable, name: "EOCIE")
                .WithFlag(3, out endOfSequenceInterruptEnable, name: "EOSIE")
                .WithFlag(4, out overrunInterruptEnable, name: "OVRIE")
                .WithValueField(5, 27, name: "IER_REST")
                .WithWriteCallback((_, __) => UpdateInterrupts())
            ;

            Registers.Control.Define(this)
                .WithFlag(0, out adcEnabled, name: "ADEN",
                    writeCallback: (_, value) =>
                    {
                        if(value)
                        {
                            adcReady.Value = true;
                        }
                    })
                .WithFlag(1, FieldMode.Write, name: "ADDIS",
                    writeCallback: (_, value) =>
                    {
                        if(value)
                        {
                            adcEnabled.Value = false;
                            adcReady.Value = false;
                            StopConversions();
                        }
                    })
                .WithFlag(2, out conversionStarted, name: "ADSTART",
                    writeCallback: (_, value) =>
                    {
                        if(value)
                        {
                            StartConversions();
                        }
                    })
                .WithFlag(3, FieldMode.Write, name: "JADSTART")
                .WithFlag(4, FieldMode.Write, name: "ADSTP",
                    writeCallback: (_, value) =>
                    {
                        if(value)
                        {
                            StopConversions();
                        }
                    })
                .WithFlag(5, FieldMode.Write, name: "JADSTP")
                .WithReservedBits(6, 22)
                .WithValueField(28, 2, name: "ADVREGEN")
                .WithFlag(30, name: "ADCALDIF")
                // La calibration se termine immédiatement : le firmware attend
                // que ADCAL retombe à 0 (hal.c:1831).
                .WithFlag(31, FieldMode.Read | FieldMode.Write, name: "ADCAL",
                    valueProviderCallback: _ => false)
                .WithWriteCallback((_, __) => UpdateInterrupts())
            ;

            Registers.Configuration.Define(this)
                .WithFlag(0, out dmaEnabled, name: "DMAEN")
                .WithFlag(1, out dmaCircular, name: "DMACFG")
                .WithReservedBits(2, 1)
                .WithValueField(3, 2, out resolution, name: "RES")
                .WithFlag(5, out alignLeft, name: "ALIGN")
                .WithValueField(6, 4, name: "EXTSEL")
                .WithValueField(10, 2, name: "EXTEN")
                .WithFlag(12, name: "OVRMOD")
                .WithFlag(13, out continuousMode, name: "CONT")
                .WithFlag(14, name: "AUTDLY")
                .WithFlag(15, name: "AUTOFF")
                .WithFlag(16, name: "DISCEN")
                .WithValueField(17, 3, name: "DISCNUM")
                .WithFlag(20, name: "JDISCEN")
                .WithValueField(21, 11, name: "CFGR_REST")
            ;

            // Zone réservée du F303 à laquelle la lib standard periph accède quand même
            Registers.Reserved0.Define(this).WithValueField(0, 32, name: "RESERVED0");
            Registers.SampleTime1.Define(this).WithValueField(0, 32, name: "SMPR1");
            Registers.SampleTime2.Define(this).WithValueField(0, 32, name: "SMPR2");
            Registers.WatchdogThreshold1.Define(this).WithValueField(0, 32, name: "TR1");
            Registers.WatchdogThreshold2.Define(this).WithValueField(0, 32, name: "TR2");
            Registers.WatchdogThreshold3.Define(this).WithValueField(0, 32, name: "TR3");

            // SQR1 : L sur 4 bits puis SQ1..SQ4 ; SQR2..SQR4 : cinq rangs chacun.
            Registers.RegularSequence1.Define(this)
                .WithValueField(0, 4, out sequenceLength, name: "L")
                .WithReservedBits(4, 2)
                .WithValueField(6, 5, out sequence[0], name: "SQ1")
                .WithReservedBits(11, 1)
                .WithValueField(12, 5, out sequence[1], name: "SQ2")
                .WithReservedBits(17, 1)
                .WithValueField(18, 5, out sequence[2], name: "SQ3")
                .WithReservedBits(23, 1)
                .WithValueField(24, 5, out sequence[3], name: "SQ4")
                .WithReservedBits(29, 3)
            ;
            DefineSequenceRegister(Registers.RegularSequence2, 4);
            DefineSequenceRegister(Registers.RegularSequence3, 9);
            DefineSequenceRegister(Registers.RegularSequence4, 14, count: 2);

            Registers.Data.Define(this)
                .WithValueField(0, 16, FieldMode.Read, name: "RDATA",
                    valueProviderCallback: _ =>
                    {
                        endOfConversion.Value = false;
                        UpdateInterrupts();
                        return dataRegister;
                    })
                .WithReservedBits(16, 16)
            ;

            Registers.CalibrationFactor.Define(this)
                .WithValueField(0, 7, FieldMode.Read, name: "CALFACT_S")
                .WithReservedBits(7, 9)
                .WithValueField(16, 7, FieldMode.Read, name: "CALFACT_D")
                .WithReservedBits(23, 9)
            ;

            Registers.DifferentialMode.Define(this).WithValueField(0, 32, name: "DIFSEL");
            Registers.CommonStatus.Define(this).WithValueField(0, 32, FieldMode.Read, name: "CSR");
            Registers.CommonControl.Define(this).WithValueField(0, 32, name: "CCR");
        }

        private void DefineSequenceRegister(Registers register, int firstRank, int count = 5)
        {
            var definition = register.Define(this);
            var bitOffset = 0;
            for(var i = 0; i < count; i++)
            {
                definition
                    .WithValueField(bitOffset, 5, out sequence[firstRank + i], name: $"SQ{firstRank + i + 1}")
                    .WithReservedBits(bitOffset + 5, 1);
                bitOffset += 6;
            }
            definition.WithReservedBits(bitOffset, 32 - bitOffset);
        }

        private void StartConversions()
        {
            if(!adcEnabled.Value)
            {
                this.Log(LogLevel.Warning, "Conversion demandée alors que l'ADC est désactivé");
                return;
            }
            sequenceIndex = 0;
            conversionTimer.Enabled = true;
        }

        private void StopConversions()
        {
            conversionTimer.Enabled = false;
            conversionStarted.Value = false;
        }

        private void PerformConversion()
        {
            var channel = (int)sequence[sequenceIndex].Value;
            dataRegister = VoltageToSample(channel);
            endOfConversion.Value = true;

            // Le DMA lit ensuite DR sur le bus, ce qui acquitte EOC.
            if(dmaEnabled.Value && dma != null && dmaChannel > 0)
            {
                dma.RequestTransfer(dmaChannel);
            }

            sequenceIndex++;
            if(sequenceIndex > (int)sequenceLength.Value)
            {
                sequenceIndex = 0;
                endOfSequence.Value = true;
                if(!continuousMode.Value)
                {
                    StopConversions();
                }
            }
            UpdateInterrupts();
        }

        private uint VoltageToSample(int channel)
        {
            if(channel < 0 || channel >= ChannelCount)
            {
                this.Log(LogLevel.Warning, "Canal {0} inexistant dans la séquence", channel);
                return 0;
            }

            var bits = ResolutionInBits();
            var maximum = (1 << bits) - 1;
            var raw = (double)voltages[channel] / referenceVoltage * maximum;
            var sample = (uint)Math.Round(Math.Max(0.0, Math.Min(raw, maximum)));
            return alignLeft.Value ? sample << (16 - bits) : sample;
        }

        private int ResolutionInBits()
        {
            switch(resolution.Value)
            {
                case 0: return 12;
                case 1: return 10;
                case 2: return 8;
                default: return 6;
            }
        }

        private void UpdateInterrupts()
        {
            var irq = (adcReady.Value && adcReadyInterruptEnable.Value)
                || (endOfConversion.Value && endOfConversionInterruptEnable.Value)
                || (endOfSequence.Value && endOfSequenceInterruptEnable.Value)
                || (overrun.Value && overrunInterruptEnable.Value);
            IRQ.Set(irq);
        }

        private readonly double referenceVoltage;
        private readonly int dmaChannel;
        private readonly IDMA dma;
        private readonly LimitTimer conversionTimer;
        private readonly decimal[] voltages;
        private readonly IValueRegisterField[] sequence;

        private int sequenceIndex;
        private uint dataRegister;

        private IFlagRegisterField adcEnabled;
        private IFlagRegisterField adcReady;
        private IFlagRegisterField endOfSampling;
        private IFlagRegisterField endOfConversion;
        private IFlagRegisterField endOfSequence;
        private IFlagRegisterField overrun;
        private IFlagRegisterField conversionStarted;
        private IFlagRegisterField dmaEnabled;
        private IFlagRegisterField dmaCircular;
        private IFlagRegisterField alignLeft;
        private IFlagRegisterField continuousMode;
        private IFlagRegisterField adcReadyInterruptEnable;
        private IFlagRegisterField endOfConversionInterruptEnable;
        private IFlagRegisterField endOfSequenceInterruptEnable;
        private IFlagRegisterField overrunInterruptEnable;
        private IValueRegisterField sequenceLength;
        private IValueRegisterField resolution;

        private const int ChannelCount = 19;              // IN0..IN18 (16 = temp, 18 = Vrefint)
        private const int MaximumSequenceLength = 16;

        private enum Registers
        {
            InterruptAndStatus = 0x00,
            InterruptEnable = 0x04,
            Control = 0x08,
            Configuration = 0x0C,
            Reserved0 = 0x10,
            SampleTime1 = 0x14,
            SampleTime2 = 0x18,
            WatchdogThreshold1 = 0x20,
            WatchdogThreshold2 = 0x24,
            WatchdogThreshold3 = 0x28,
            RegularSequence1 = 0x30,
            RegularSequence2 = 0x34,
            RegularSequence3 = 0x38,
            RegularSequence4 = 0x3C,
            Data = 0x40,
            DifferentialMode = 0xB0,
            CalibrationFactor = 0xB4,
            // Registres communs ADC1_2, vus depuis la base d'ADC1
            CommonStatus = 0x300,
            CommonControl = 0x308,
        }
    }
}
