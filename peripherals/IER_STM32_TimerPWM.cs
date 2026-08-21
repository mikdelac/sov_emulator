//
// Modèle de timer STM32 avec canaux de comparaison (RM0316 §21-22), écrit pour
// l'émulation du firmware IER.
//
// Le modèle intégré Timers.STM32_Timer ne modélise que la base de temps : les
// registres CCR1..CCR4 ne mémorisent rien, aucun événement de comparaison n'est
// produit, CCxIF n'est jamais levé et la ligne CaptureCompareInterrupt reste
// muette. Le firmware IER pilote pourtant toute sa puissance depuis ces
// interruptions : TIM8_CC_IRQHandler et TIM4_IRQHandler (../ier/src/hal.c) sont
// les seuls appelants de PWM_write(), donc les seuls écrivains de CCR. Avec le
// modèle intégré, le SSR de l'élément chauffant, le ventilateur SBX et la
// recopie 0-10 V restent figés pour toute la session : la régulation calcule un
// rapport cyclique correct que rien ne transmet aux sorties.
//
// Ce modèle implémente le comptage progressif, ARR et PSC avec leur préchargement,
// les quatre voies de comparaison (CCR, CCMR OCxM/OCxPE, CCER CCxE/CCxP), les
// drapeaux UIF/CCxIF/CCxOF, EGR et BDTR.MOE, ainsi que les cinq lignes
// d'interruption des timers avancés plus une ligne combinée pour les timers
// généraux, qui n'en ont qu'une côté NVIC.
//
// Le rapport cyclique effectif de chaque voie se relit depuis le moniteur :
//     sysbus.timer8 GetDutyCycle 1
//
using System;

using Antmicro.Renode.Core;
using Antmicro.Renode.Core.Structure.Registers;
using Antmicro.Renode.Exceptions;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Time;

namespace Antmicro.Renode.Peripherals.Timers
{
    [AllowedTranslations(AllowedTranslation.WordToDoubleWord | AllowedTranslation.ByteToDoubleWord)]
    public class IER_STM32_TimerPWM : BasicDoubleWordPeripheral, IKnownSize
    {
        // `advanced` distingue TIM1/TIM8 (BDTR.MOE, quatre lignes NVIC) des
        // timers généraux, dont les sorties sont validées par le seul CCxE.
        public IER_STM32_TimerPWM(IMachine machine, long frequency, ulong initialLimit = 0xFFFF,
                                  bool advanced = false) : base(machine)
        {
            this.initialLimit = initialLimit;
            this.advanced = advanced;

            IRQ = new GPIO();
            UpdateInterrupt = new GPIO();
            CaptureCompareInterrupt = new GPIO();
            TriggerInterrupt = new GPIO();
            CommutationInterrupt = new GPIO();
            BreakInterrupt = new GPIO();

            counterTimer = new LimitTimer(machine.ClockSource, (ulong)frequency, this, "counter",
                limit: initialLimit + 1, direction: Direction.Ascending, enabled: false,
                eventEnabled: true, autoUpdate: true);
            counterTimer.LimitReached += OnUpdateEvent;

            compareTimer = new LimitTimer[ChannelCount];
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                var index = channel;
                // Un temporisateur par voie, de même période que le compteur et
                // calé en phase sur CCRx : il produit une correspondance par
                // période sans jamais être réarmé, ce qui évite qu'un événement
                // de mise à jour tombant dans le même quantum ne l'annule.
                compareTimer[index] = new LimitTimer(machine.ClockSource, (ulong)frequency, this, $"compare{index + 1}",
                    limit: 1, direction: Direction.Ascending, enabled: false,
                    eventEnabled: true, autoUpdate: true);
                compareTimer[index].LimitReached += () => OnCompareEvent(index);
            }

            compareValue = new uint[ChannelCount];
            comparePreload = new uint[ChannelCount];
            compareFlag = new IFlagRegisterField[ChannelCount];
            compareOverflowFlag = new IFlagRegisterField[ChannelCount];
            compareInterruptEnable = new IFlagRegisterField[ChannelCount];
            compareOutputEnable = new IFlagRegisterField[ChannelCount];
            captureCompareMode = new IValueRegisterField[2];

            DefineRegisters();
            Reset();
        }

        public override void Reset()
        {
            counterTimer.Enabled = false;
            counterTimer.Limit = initialLimit + 1;
            counterTimer.Divider = 1;
            counterTimer.Value = 0;
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                compareTimer[channel].Enabled = false;
                compareTimer[channel].Divider = 1;
                compareValue[channel] = 0;
                comparePreload[channel] = 0;
            }
            autoReload = initialLimit;
            autoReloadPreload = initialLimit;
            prescaler = 0;
            prescalerPreload = 0;
            unsupportedModeReported = false;
            base.Reset();
            UpdateInterrupts();
        }

        public long Size => 0x400;

        // Lignes séparées des timers avancés ; le .repl ne câble que celles qui
        // existent réellement sur le timer décrit.
        public GPIO UpdateInterrupt { get; }
        public GPIO CaptureCompareInterrupt { get; }
        public GPIO TriggerInterrupt { get; }
        public GPIO CommutationInterrupt { get; }
        public GPIO BreakInterrupt { get; }

        // Ligne unique des timers généraux : ou logique de toutes les sources.
        public GPIO IRQ { get; }

        // Rapport cyclique réellement présenté sur la broche, en pour cent.
        // C'est la seule observation possible de la sortie : Renode ne relie pas
        // les fonctions alternées des GPIO aux timers.
        public double GetDutyCycle(int channel)
        {
            var index = channel - 1;
            if(index < 0 || index >= ChannelCount)
            {
                throw new RecoverableException($"Voie {channel} hors plage (1-{ChannelCount})");
            }
            if(!IsOutputDriven(index))
            {
                return 0.0;
            }
            var period = (double)autoReload + 1;
            var ratio = Math.Min(compareValue[index], autoReload + 1) / period;
            if(OutputCompareMode(index) == ModePwm2)
            {
                ratio = 1.0 - ratio;
            }
            return 100.0 * ratio;
        }

        // -- base de temps -----------------------------------------------------
        private void OnUpdateEvent()
        {
            // Rechargement des registres à préchargement, comme au passage par
            // l'événement de mise à jour matériel. Les comparateurs ne sont
            // recalés que si l'un d'eux a bougé : les recaler à chaque période
            // reviendrait à annuler une correspondance imminente.
            var reloaded = LoadPrescaler() | LoadAutoReload() | LoadComparePreloads();

            if(!updateDisable.Value)
            {
                updateFlag.Value = true;
            }
            if(onePulseMode.Value)
            {
                counterEnable.Value = false;
                counterTimer.Enabled = false;
            }
            if(reloaded)
            {
                ArmCompares();
            }
            UpdateInterrupts();
        }

        private void OnCompareEvent(int channel)
        {
            // La première correspondance après un calage est atteinte avec une
            // limite raccourcie ; on la rallonge ici à la période entière, ce
            // qui remet aussi le comparateur à zéro, donc en phase.
            var period = autoReload + 1;
            if(compareTimer[channel].Limit != period)
            {
                compareTimer[channel].Limit = period;
            }
            RaiseCompare(channel);
        }

        private void RaiseCompare(int channel)
        {
            if(compareFlag[channel].Value)
            {
                compareOverflowFlag[channel].Value = true;
            }
            compareFlag[channel].Value = true;
            UpdateInterrupts();
        }

        // Position du compteur, bornée à ARR comme le registre CNT matériel.
        private ulong CurrentCount()
        {
            var value = counterTimer.Value;
            return value > autoReload ? autoReload : value;
        }

        private void ArmCompares()
        {
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                ArmCompare(channel);
            }
        }

        // Cale le comparateur de la voie sur CCRx. Rien n'est armé si CCRx
        // dépasse ARR : le compteur ne l'atteint alors jamais, aucun événement
        // n'est produit et OCxREF reste maintenu à 1 (RM0316 §22.3.10).
        private void ArmCompare(int channel)
        {
            var timer = compareTimer[channel];
            var target = (ulong)compareValue[channel];
            if(!counterEnable.Value || target > autoReload)
            {
                timer.Enabled = false;
                return;
            }
            var period = autoReload + 1;
            var current = CurrentCount();
            if(target == current)
            {
                // CCRx = 0 en début de période, ou écriture pile sur la position
                // courante : la correspondance est immédiate.
                RaiseCompare(channel);
            }
            // LimitTimer n'accepte pas qu'on lui impose une phase par Value :
            // le comparateur est donc lancé avec la distance qui reste jusqu'à
            // la prochaine correspondance, puis rallongé à la période entière
            // dans OnCompareEvent.
            var remaining = target > current ? target - current : period - current + target;
            timer.Enabled = false;
            timer.Divider = prescaler + 1;
            timer.Limit = remaining;
            timer.Enabled = true;
        }

        // Recopie les registres de comparaison préchargés ; rend vrai si l'un
        // d'eux a changé, donc si les comparateurs doivent être recalés.
        private bool LoadComparePreloads()
        {
            var reloaded = false;
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                if(IsComparePreloaded(channel) && compareValue[channel] != comparePreload[channel])
                {
                    compareValue[channel] = comparePreload[channel];
                    reloaded = true;
                }
            }
            return reloaded;
        }

        private void UpdateCounterState()
        {
            counterTimer.Enabled = counterEnable.Value;
            if(!counterEnable.Value)
            {
                for(var channel = 0; channel < ChannelCount; channel++)
                {
                    compareTimer[channel].Enabled = false;
                }
                return;
            }
            ArmCompares();
        }

        private bool LoadPrescaler()
        {
            if(prescaler == prescalerPreload)
            {
                return false;
            }
            prescaler = prescalerPreload;
            counterTimer.Divider = prescaler + 1;
            return true;
        }

        private bool LoadAutoReload()
        {
            if(autoReload == autoReloadPreload)
            {
                return false;
            }
            autoReload = autoReloadPreload;
            counterTimer.Limit = autoReload + 1;
            return true;
        }

        private void GenerateUpdate()
        {
            // UG remet le compteur à zéro et recharge les registres préchargés ;
            // URS = 1 réserve UIF aux débordements réels.
            LoadPrescaler();
            LoadAutoReload();
            LoadComparePreloads();
            counterTimer.Value = 0;
            if(!updateDisable.Value && !updateRequestSource.Value)
            {
                updateFlag.Value = true;
            }
            ArmCompares();
            UpdateInterrupts();
        }

        private void UpdateInterrupts()
        {
            var compare = false;
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                compare |= compareFlag[channel].Value && compareInterruptEnable[channel].Value;
            }
            var update = updateFlag.Value && updateInterruptEnable.Value;
            var trigger = triggerFlag.Value && triggerInterruptEnable.Value;
            var commutation = commutationFlag.Value && commutationInterruptEnable.Value;
            var brk = breakFlag.Value && breakInterruptEnable.Value;

            UpdateInterrupt.Set(update);
            CaptureCompareInterrupt.Set(compare);
            TriggerInterrupt.Set(trigger);
            CommutationInterrupt.Set(commutation);
            BreakInterrupt.Set(brk);
            IRQ.Set(update || compare || trigger || commutation || brk);
        }

        // -- décodage de CCMR1 / CCMR2 -----------------------------------------
        private uint OutputCompareMode(int channel)
        {
            var raw = (uint)captureCompareMode[channel / 2].Value;
            var shift = (channel % 2 == 0) ? 4 : 12;
            return (raw >> shift) & 0x7;
        }

        private bool IsComparePreloaded(int channel)
        {
            var raw = (uint)captureCompareMode[channel / 2].Value;
            var shift = (channel % 2 == 0) ? 3 : 11;
            return ((raw >> shift) & 0x1) != 0;
        }

        private bool IsOutputDriven(int channel)
        {
            if(!compareOutputEnable[channel].Value)
            {
                return false;
            }
            if(advanced && !mainOutputEnable.Value)
            {
                return false;
            }
            var mode = OutputCompareMode(channel);
            return mode == ModePwm1 || mode == ModePwm2;
        }

        private void WriteCompare(int channel, ulong value)
        {
            comparePreload[channel] = (uint)value;
            if(IsComparePreloaded(channel))
            {
                return;
            }
            compareValue[channel] = (uint)value;
            ArmCompare(channel);
        }

        private void ReportUnsupportedMode()
        {
            if(unsupportedModeReported)
            {
                return;
            }
            unsupportedModeReported = true;
            this.Log(LogLevel.Warning,
                "Comptage dégressif ou aligné au centre demandé : le modèle ne connaît que le comptage progressif.");
        }

        private void DefineRegisters()
        {
            Registers.Control1.Define(this)
                .WithFlag(0, out counterEnable, name: "CEN",
                    writeCallback: (_, __) => UpdateCounterState())
                .WithFlag(1, out updateDisable, name: "UDIS")
                .WithFlag(2, out updateRequestSource, name: "URS")
                .WithFlag(3, out onePulseMode, name: "OPM")
                .WithFlag(4, name: "DIR",
                    writeCallback: (_, value) => { if(value) ReportUnsupportedMode(); })
                .WithValueField(5, 2, name: "CMS",
                    writeCallback: (_, value) => { if(value != 0) ReportUnsupportedMode(); })
                .WithFlag(7, out autoReloadPreloadEnable, name: "ARPE")
                .WithValueField(8, 2, name: "CKD")
                .WithFlag(10, name: "UIFREMAP")
                .WithValueField(11, 21, name: "RESERVED", valueProviderCallback: _ => 0);

            Registers.Control2.Define(this)
                .WithValueField(0, 32, name: "CR2");

            Registers.SlaveModeControl.Define(this)
                .WithValueField(0, 32, name: "SMCR");

            var dier = Registers.DmaInterruptEnable.Define(this)
                .WithFlag(0, out updateInterruptEnable, name: "UIE");
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                dier = dier.WithFlag(1 + channel, out compareInterruptEnable[channel], name: $"CC{channel + 1}IE");
            }
            dier.WithFlag(5, out commutationInterruptEnable, name: "COMIE")
                .WithFlag(6, out triggerInterruptEnable, name: "TIE")
                .WithFlag(7, out breakInterruptEnable, name: "BIE")
                .WithValueField(8, 7, name: "xDE")
                .WithValueField(15, 17, name: "RESERVED", valueProviderCallback: _ => 0)
                .WithWriteCallback((_, __) => UpdateInterrupts());

            var status = Registers.Status.Define(this)
                .WithFlag(0, out updateFlag, FieldMode.Read | FieldMode.WriteZeroToClear, name: "UIF");
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                status = status.WithFlag(1 + channel, out compareFlag[channel],
                    FieldMode.Read | FieldMode.WriteZeroToClear, name: $"CC{channel + 1}IF");
            }
            status = status.WithFlag(5, out commutationFlag, FieldMode.Read | FieldMode.WriteZeroToClear, name: "COMIF")
                .WithFlag(6, out triggerFlag, FieldMode.Read | FieldMode.WriteZeroToClear, name: "TIF")
                .WithFlag(7, out breakFlag, FieldMode.Read | FieldMode.WriteZeroToClear, name: "BIF")
                .WithFlag(8, FieldMode.Read | FieldMode.WriteZeroToClear, name: "B2IF");
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                status = status.WithFlag(9 + channel, out compareOverflowFlag[channel],
                    FieldMode.Read | FieldMode.WriteZeroToClear, name: $"CC{channel + 1}OF");
            }
            status.WithValueField(13, 19, name: "RESERVED", valueProviderCallback: _ => 0)
                .WithWriteCallback((_, __) => UpdateInterrupts());

            var egr = Registers.EventGeneration.Define(this)
                .WithFlag(0, FieldMode.Write, name: "UG",
                    writeCallback: (_, value) => { if(value) GenerateUpdate(); });
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                var index = channel;
                egr = egr.WithFlag(1 + index, FieldMode.Write, name: $"CC{index + 1}G",
                    writeCallback: (_, value) => { if(value) RaiseCompare(index); });
            }
            egr.WithFlag(5, FieldMode.Write, name: "COMG")
                .WithFlag(6, FieldMode.Write, name: "TG",
                    writeCallback: (_, value) => { if(value) { triggerFlag.Value = true; UpdateInterrupts(); } })
                .WithFlag(7, FieldMode.Write, name: "BG",
                    writeCallback: (_, value) => { if(value) { breakFlag.Value = true; UpdateInterrupts(); } })
                .WithValueField(8, 24, name: "RESERVED", valueProviderCallback: _ => 0);

            // CCMR1/CCMR2 sont gardés bruts : seuls OCxM et OCxPE sont décodés,
            // les modes capture n'ayant aucun usage dans ce firmware.
            Registers.CaptureCompareMode1.Define(this)
                .WithValueField(0, 32, out captureCompareMode[0], name: "CCMR1");
            Registers.CaptureCompareMode2.Define(this)
                .WithValueField(0, 32, out captureCompareMode[1], name: "CCMR2");

            var ccer = Registers.CaptureCompareEnable.Define(this);
            for(var channel = 0; channel < ChannelCount; channel++)
            {
                ccer = ccer.WithFlag(4 * channel, out compareOutputEnable[channel], name: $"CC{channel + 1}E")
                    .WithFlag(4 * channel + 1, name: $"CC{channel + 1}P")
                    .WithFlag(4 * channel + 2, name: $"CC{channel + 1}NE")
                    .WithFlag(4 * channel + 3, name: $"CC{channel + 1}NP");
            }
            ccer.WithValueField(16, 16, name: "RESERVED", valueProviderCallback: _ => 0);

            Registers.Counter.Define(this)
                .WithValueField(0, 32, name: "CNT",
                    valueProviderCallback: _ => CurrentCount(),
                    writeCallback: (_, value) =>
                    {
                        counterTimer.Value = value > autoReload ? autoReload : value;
                        ArmCompares();
                    });

            Registers.Prescaler.Define(this)
                .WithValueField(0, 16, name: "PSC",
                    valueProviderCallback: _ => prescalerPreload,
                    writeCallback: (_, value) => prescalerPreload = (uint)value)
                .WithValueField(16, 16, name: "RESERVED", valueProviderCallback: _ => 0);

            Registers.AutoReload.Define(this, 0xFFFFFFFF)
                .WithValueField(0, 32, name: "ARR",
                    valueProviderCallback: _ => autoReloadPreload,
                    writeCallback: (_, value) =>
                    {
                        // ARR = 0 arrêterait le compteur ; le matériel l'interdit
                        // de fait puisque la période vaut ARR + 1.
                        autoReloadPreload = value;
                        if(!autoReloadPreloadEnable.Value && LoadAutoReload())
                        {
                            ArmCompares();
                        }
                    });

            Registers.RepetitionCounter.Define(this)
                .WithValueField(0, 32, name: "RCR");

            for(var channel = 0; channel < ChannelCount; channel++)
            {
                var index = channel;
                ((Registers)((long)Registers.CaptureCompare1 + 4 * index)).Define(this)
                    .WithValueField(0, 32, name: $"CCR{index + 1}",
                        valueProviderCallback: _ => compareValue[index],
                        writeCallback: (_, value) => WriteCompare(index, value));
            }

            Registers.BreakAndDeadTime.Define(this)
                .WithValueField(0, 15, name: "DTG/LOCK/OSSI/OSSR/BKE/BKP/AOE")
                .WithFlag(15, out mainOutputEnable, name: "MOE")
                .WithValueField(16, 16, name: "RESERVED", valueProviderCallback: _ => 0);

            Registers.DmaControl.Define(this)
                .WithValueField(0, 32, name: "DCR");
            Registers.DmaAddress.Define(this)
                .WithValueField(0, 32, name: "DMAR");
            Registers.Option.Define(this)
                .WithValueField(0, 32, name: "OR");
        }

        private readonly ulong initialLimit;
        private readonly bool advanced;

        private readonly LimitTimer counterTimer;
        private readonly LimitTimer[] compareTimer;
        private readonly uint[] compareValue;
        private readonly uint[] comparePreload;

        private ulong autoReload;
        private ulong autoReloadPreload;
        private uint prescaler;
        private uint prescalerPreload;
        private bool unsupportedModeReported;

        private IFlagRegisterField counterEnable;
        private IFlagRegisterField updateDisable;
        private IFlagRegisterField updateRequestSource;
        private IFlagRegisterField onePulseMode;
        private IFlagRegisterField autoReloadPreloadEnable;
        private IFlagRegisterField mainOutputEnable;

        private IFlagRegisterField updateFlag;
        private IFlagRegisterField commutationFlag;
        private IFlagRegisterField triggerFlag;
        private IFlagRegisterField breakFlag;
        private IFlagRegisterField updateInterruptEnable;
        private IFlagRegisterField commutationInterruptEnable;
        private IFlagRegisterField triggerInterruptEnable;
        private IFlagRegisterField breakInterruptEnable;

        private readonly IFlagRegisterField[] compareFlag;
        private readonly IFlagRegisterField[] compareOverflowFlag;
        private readonly IFlagRegisterField[] compareInterruptEnable;
        private readonly IFlagRegisterField[] compareOutputEnable;
        private readonly IValueRegisterField[] captureCompareMode;

        private const int ChannelCount = 4;
        private const uint ModePwm1 = 0x6;
        private const uint ModePwm2 = 0x7;

        private enum Registers : long
        {
            Control1 = 0x00,
            Control2 = 0x04,
            SlaveModeControl = 0x08,
            DmaInterruptEnable = 0x0C,
            Status = 0x10,
            EventGeneration = 0x14,
            CaptureCompareMode1 = 0x18,
            CaptureCompareMode2 = 0x1C,
            CaptureCompareEnable = 0x20,
            Counter = 0x24,
            Prescaler = 0x28,
            AutoReload = 0x2C,
            RepetitionCounter = 0x30,
            CaptureCompare1 = 0x34,
            CaptureCompare2 = 0x38,
            CaptureCompare3 = 0x3C,
            CaptureCompare4 = 0x40,
            BreakAndDeadTime = 0x44,
            DmaControl = 0x48,
            DmaAddress = 0x4C,
            Option = 0x50,
        }
    }
}
