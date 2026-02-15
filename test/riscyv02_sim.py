# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Behavioral emulator for RISCY-V02.
#
# Executes instructions atomically and produces bus transaction sequences
# from the ISA spec. Used for differential fuzz testing against the RTL.
#
# Bus model:
#   _execute() returns only memory-phase bus entries (load/store
#   addresses). _dispatch() prepends the pipelined fetch of the next
#   instruction and, for redirects, appends the target fetch. This
#   separation keeps fetch logic out of instruction handlers.
#
#   After the execute entries (fetch + mem), the CPU becomes
#   interruptible. If no interrupt fires and the instruction changed PC
#   (redirect), a target fetch from the new PC is appended automatically.
#
# Store visibility:
#   An instruction's stores are never visible to the immediately following
#   instruction fetch (the fetch is pipelined ahead of the stores). Stores
#   ARE visible to subsequent load data reads. This is modeled by deferring
#   store commits: _dispatch() reads ir from RAM first, then commits
#   pending stores, then runs _execute() (whose loads see the new values).
#
# Interrupt model:
#   The CPU checks for pending interrupts:
#     1. At every dispatch boundary (between instructions).
#     2. After the execute entries of the current instruction, before each
#        remaining bus entry (the target fetch for redirects).
#   If an interrupt fires during a redirect's target fetch, the target
#   instruction never executes. EPC captures the target address; RETI
#   will re-fetch it.


def sext8(v):
    """Sign-extend 8-bit value to Python int."""
    return v - 256 if v & 0x80 else v


def sext10(v):
    """Sign-extend 10-bit value to Python int."""
    return v - 1024 if v & 0x200 else v


def to_signed16(v):
    """Interpret 16-bit unsigned as signed."""
    return v - 0x10000 if v >= 0x8000 else v


class RISCYV02Sim:
    """Behavioral emulator for RISCY-V02 CPU.

    Bus protocol (active-edge timing):
      posedge → address phase: AB_lo on uo_out, AB_hi on uio_out, uio_oe=0xFF
      negedge → data phase: {SYNC,RWB} on uo_out, DO on uio_out, uio_oe per RWB

    Usage:
      posedge_outputs() → (uo, uio, oe) for address phase
      negedge_outputs() → (uo, uio, oe) for data phase
      tick(irqb, nmib, rdy) after negedge comparison, advances to next cycle
    """

    def __init__(self, ram):
        self.ram = bytearray(ram)
        self.pc = 0
        self.regs = [0] * 8
        self.epc = 0
        self.i_bit = True       # True = interrupts disabled
        self.waiting = False
        self.stopped = False

        # NMI state
        self.nmi_prev = True    # Previous NMIB level (active-low)
        self.nmi_pending = False
        self.nmi_ack = False

        # Bus state (set by tick, used by output methods)
        # After init: cycle 0 = fetch hi byte (lo byte was fetched at reset)
        self.current_addr = 0x0001
        self.current_rwb = True
        self.current_dout = 0
        self.current_sync = False
        self.cpu_active = True

        # Bus sequencer
        self._bus_seq = []      # List of (addr, rwb, dout)
        self._bus_idx = 0       # Next entry to consume
        self._interrupt_point = 0  # Index where CPU becomes interruptible
        self._pending_stores = []  # Deferred stores: [(addr, data), ...]
        self.last_dispatch = ""   # Human-readable dispatch info for tracing

    # ------------------------------------------------------------------
    # Output methods
    # ------------------------------------------------------------------

    def posedge_outputs(self):
        """Address-phase outputs: AB_lo, AB_hi, 0xFF."""
        return (self.current_addr & 0xFF,
                (self.current_addr >> 8) & 0xFF,
                0xFF)

    def negedge_outputs(self):
        """Data-phase outputs: {SYNC, RWB}, DO, OE."""
        rwb_bit = 1 if self.current_rwb else 0
        sync_bit = 1 if self.current_sync else 0
        uo = (sync_bit << 1) | rwb_bit
        uio = self.current_dout if not self.current_rwb else 0
        oe = 0x00 if self.current_rwb else 0xFF
        return (uo, uio, oe)

    # ------------------------------------------------------------------
    # Interrupt check
    # ------------------------------------------------------------------

    def _check_interrupt(self, irqb, nmi_edge):
        """Check for pending interrupts. Returns True if one was taken."""
        take_nmi = (self.nmi_pending or nmi_edge) and not self.nmi_ack
        take_irq = not irqb and not self.i_bit and not take_nmi
        if take_nmi or take_irq:
            self._take_interrupt(take_nmi)
            return True
        return False

    # ------------------------------------------------------------------
    # Tick — one CPU clock cycle
    # ------------------------------------------------------------------

    def tick(self, irqb, nmib, rdy):
        """Advance one clock cycle. Called at falling edge."""
        # NMI edge detection (ungated — runs every cycle)
        nmi_edge = self.nmi_prev and not nmib
        self.nmi_prev = nmib

        # Snapshot pre-update values (simultaneous updates)
        old_nmi_ack = self.nmi_ack
        old_nmi_pending = self.nmi_pending

        # nmi_pending update (ungated, every cycle)
        if old_nmi_ack:
            self.nmi_pending = False
        elif nmi_edge:
            self.nmi_pending = True

        # Clock gating
        wake = self.nmi_pending or nmi_edge or not irqb
        self.cpu_active = rdy and not self.stopped and (not self.waiting or wake)
        if not self.cpu_active:
            return

        if old_nmi_ack and not old_nmi_pending:
            self.nmi_ack = False

        if self.waiting:
            self.waiting = False

        # Stores are not visible to the immediately following instruction
        # fetch (pipelined ahead), so defer commits until _dispatch().
        if not self.current_rwb:
            self._pending_stores.append((self.current_addr, self.current_dout))

        # Dispatch if bus sequence exhausted
        self.current_sync = False
        if self._bus_idx >= len(self._bus_seq):
            self._dispatch(irqb, nmi_edge)
            self._bus_idx = 0

        # After the execute phase, the CPU is interruptible. Check before
        # consuming each remaining entry (the target fetch for redirects).
        if self._bus_idx >= self._interrupt_point:
            if self._check_interrupt(irqb, nmi_edge):
                self._bus_idx = 0

        # Consume next bus entry
        addr, rwb, dout = self._bus_seq[self._bus_idx]
        self.current_addr = addr & 0xFFFF
        self.current_rwb = rwb
        self.current_dout = dout & 0xFF
        self._bus_idx += 1

    # ------------------------------------------------------------------
    # Bus sequence helpers
    # ------------------------------------------------------------------

    def _fetch_seq(self, pc):
        """Fetch sequence: lo byte, hi byte."""
        return [(pc & 0xFFFF, True, 0), ((pc | 1) & 0xFFFF, True, 0)]

    def _stale_addr(self, pc):
        """Stale address hold (3-cycle instructions re-present fetch addr)."""
        return [(pc & 0xFFFF, True, 0)]

    # ------------------------------------------------------------------
    # Dispatch — fetch and execute one instruction
    # ------------------------------------------------------------------

    def _commit_pending_stores(self):
        """Commit all deferred stores to RAM."""
        for addr, data in self._pending_stores:
            self.ram[addr] = data
        self._pending_stores = []

    def _dispatch(self, irqb, nmi_edge):
        """Dispatch the next instruction or take an interrupt."""
        if self._check_interrupt(irqb, nmi_edge):
            self._commit_pending_stores()
            return

        # Fetch ir before committing stores (not visible to next fetch),
        # then commit before execute (visible to loads).
        fetch_pc = self.pc
        lo = self.ram[self.pc & 0xFFFF]
        hi = self.ram[(self.pc | 1) & 0xFFFF]
        ir = (hi << 8) | lo
        self.pc = (self.pc + 2) & 0xFFFF
        self._commit_pending_stores()

        # BRK/INT detection: system prefix + sub[5]=1
        is_int = (ir >> 6) == 0b1111100000 and (ir >> 5) & 1
        if is_int:
            self.pc = (self.pc & 0xFFFE) | (1 if self.i_bit else 0)
            self.i_bit = True

        # Execute: applies architectural effects, returns memory bus entries.
        # Dispatch owns the fetch; execute only produces mem-phase entries.
        next_pc = self.pc
        self._redirect = False
        regs_before = list(self.regs)
        mem_entries = self._execute(ir)
        exec_entries = self._fetch_seq(next_pc) + mem_entries

        # If a redirect occurred, append target fetch from new PC.
        if self._redirect:
            self._bus_seq = exec_entries + self._fetch_seq(self.pc)
        else:
            self._bus_seq = exec_entries
        self._interrupt_point = len(exec_entries)
        self.current_sync = True

        self.last_dispatch = (
            f"INSN @0x{fetch_pc:04X} ir=0x{ir:04X}"
            f" regs={['%04X' % r for r in regs_before]}"
            f" -> pc=0x{self.pc:04X}"
            f" redir={self._redirect} seq={len(self._bus_seq)}"
        )

    def _take_interrupt(self, take_nmi):
        """Enter interrupt: save PC to EPC, jump to vector."""
        modified_pc = (self.pc & 0xFFFE) | (1 if self.i_bit else 0)
        self.i_bit = True

        if take_nmi:
            self.nmi_ack = True
            self.nmi_pending = True  # Cleared by nmi_ack next cycle
            vector = 0x0002
        else:
            vector = 0x0006

        self.epc = modified_pc
        self.pc = vector
        exec_entries = self._fetch_seq(modified_pc)
        self._bus_seq = exec_entries + self._fetch_seq(vector)
        self._interrupt_point = len(exec_entries)
        self.last_dispatch = (
            f"{'NMI' if take_nmi else 'IRQ'}"
            f" epc=0x{modified_pc:04X} vec=0x{vector:04X}"
        )

    # ------------------------------------------------------------------
    # Execute — all instruction handlers
    # ------------------------------------------------------------------

    def _execute(self, ir):
        """Execute one instruction. Returns memory-phase bus entries.

        Applies architectural effects (register writes, PC changes).
        Returns only the memory bus entries (load/store addresses);
        the caller prepends the pipelined fetch and appends target
        fetch for redirects.

        Sets self._redirect = True and changes self.pc for redirects.
        """
        next_pc = self.pc   # Already advanced by 2

        prefix5 = ir >> 11

        # =================================================================
        # R,8 format (5-bit prefix): ADDI..XORIF
        # =================================================================
        if prefix5 <= 0b10000:
            rs_idx = ir & 7
            imm8_raw = (ir >> 3) & 0xFF

            if prefix5 == 0b00000:      # ADDI
                self.regs[rs_idx] = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFF
                return []

            if prefix5 == 0b00001:      # LI
                self.regs[rs_idx] = sext8(imm8_raw) & 0xFFFF
                return []

            if prefix5 == 0b00010:      # LW (word load, dest=R0)
                addr = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFF
                lo = self.ram[addr]
                hi = self.ram[(addr + 1) & 0xFFFF]
                self.regs[0] = (hi << 8) | lo
                return [(addr, True, 0),
                        ((addr + 1) & 0xFFFF, True, 0)]

            if prefix5 == 0b00011:      # LB (sign-extend byte load, dest=R0)
                addr = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFF
                byte = self.ram[addr]
                self.regs[0] = (byte | 0xFF00) if byte & 0x80 else byte
                return [(addr, True, 0)]

            if prefix5 == 0b00100:      # LBU (zero-extend byte load, dest=R0)
                addr = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFF
                self.regs[0] = self.ram[addr]
                return [(addr, True, 0)]

            if prefix5 == 0b00101:      # SW (word store, data=R0)
                addr = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFF
                lo = self.regs[0] & 0xFF
                hi = (self.regs[0] >> 8) & 0xFF
                return [(addr, False, lo),
                        ((addr + 1) & 0xFFFF, False, hi)]

            if prefix5 == 0b00110:      # SB (byte store, data=R0)
                addr = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFF
                return [(addr, False, self.regs[0] & 0xFF)]

            if prefix5 == 0b00111:      # JR
                self.pc = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFF
                self._redirect = True
                return []

            if prefix5 == 0b01000:      # JALR (link to rs)
                old_rs = self.regs[rs_idx]
                self.regs[rs_idx] = next_pc
                self.pc = (old_rs + sext8(imm8_raw)) & 0xFFFF
                self._redirect = True
                return []

            if prefix5 == 0b01001:      # ANDI (zero-ext imm)
                self.regs[rs_idx] = self.regs[rs_idx] & imm8_raw
                return []

            if prefix5 == 0b01010:      # ORI (zero-ext imm)
                self.regs[rs_idx] = self.regs[rs_idx] | imm8_raw
                return []

            if prefix5 == 0b01011:      # XORI (zero-ext imm)
                self.regs[rs_idx] = self.regs[rs_idx] ^ imm8_raw
                return []

            if prefix5 == 0b01100:      # SLTI (signed, dest=R0)
                self.regs[0] = 1 if to_signed16(self.regs[rs_idx]) < sext8(imm8_raw) else 0
                return []

            if prefix5 == 0b01101:      # SLTUI (unsigned, dest=R0)
                self.regs[0] = 1 if self.regs[rs_idx] < (sext8(imm8_raw) & 0xFFFF) else 0
                return []

            if prefix5 == 0b01110:      # BZ
                scrambled = imm8_raw
                off = (((scrambled >> 7) & 1) << 7 |
                       (scrambled & 1) << 6 |
                       (scrambled >> 1) & 0x3F)
                if self.regs[rs_idx] == 0:
                    self.pc = (next_pc + sext8(off) * 2) & 0xFFFF
                    self._redirect = True
                return []

            if prefix5 == 0b01111:      # BNZ
                scrambled = imm8_raw
                off = (((scrambled >> 7) & 1) << 7 |
                       (scrambled & 1) << 6 |
                       (scrambled >> 1) & 0x3F)
                if self.regs[rs_idx] != 0:
                    self.pc = (next_pc + sext8(off) * 2) & 0xFFFF
                    self._redirect = True
                return []

            if prefix5 == 0b10000:      # XORIF (zero-ext imm, dest=R0)
                self.regs[0] = self.regs[rs_idx] ^ imm8_raw
                return []

            # Unknown R,8 — treat as NOP
            return []

        # =================================================================
        # R,7 format (6-bit prefix): LUI, AUIPC
        # =================================================================
        prefix6 = ir >> 10
        if prefix6 == 0b110100:     # LUI
            rd = ir & 7
            imm7 = (ir >> 3) & 0x7F
            self.regs[rd] = (imm7 << 9) & 0xFFFF
            return []

        if prefix6 == 0b110101:     # AUIPC
            rd = ir & 7
            imm7 = (ir >> 3) & 0x7F
            self.regs[rd] = (next_pc + (imm7 << 9)) & 0xFFFF
            return []

        # =================================================================
        # "10" format (6-bit prefix): J, JAL
        # =================================================================
        if prefix6 == 0b110110:     # J
            off10 = ir & 0x3FF
            self.pc = (next_pc + sext10(off10) * 2) & 0xFFFF
            self._redirect = True
            return []

        if prefix6 == 0b110111:     # JAL (link to R6)
            off10 = ir & 0x3FF
            self.regs[6] = next_pc
            self.pc = (next_pc + sext10(off10) * 2) & 0xFFFF
            self._redirect = True
            return []

        # =================================================================
        # R,R,R format (7-bit prefix): ADD..SRA
        # =================================================================
        prefix7 = ir >> 9
        if 0b1110000 <= prefix7 <= 0b1111001:
            rd = (ir >> 6) & 7
            rs2 = (ir >> 3) & 7
            rs1 = ir & 7
            a = self.regs[rs1]
            b = self.regs[rs2]

            if prefix7 == 0b1110000:    self.regs[rd] = (a + b) & 0xFFFF        # ADD
            elif prefix7 == 0b1110001:  self.regs[rd] = (a - b) & 0xFFFF        # SUB
            elif prefix7 == 0b1110010:  self.regs[rd] = a & b                   # AND
            elif prefix7 == 0b1110011:  self.regs[rd] = a | b                   # OR
            elif prefix7 == 0b1110100:  self.regs[rd] = a ^ b                   # XOR
            elif prefix7 == 0b1110101:                                          # SLT
                self.regs[rd] = 1 if to_signed16(a) < to_signed16(b) else 0
            elif prefix7 == 0b1110110:                                          # SLTU
                self.regs[rd] = 1 if a < b else 0
            elif prefix7 == 0b1110111:  self.regs[rd] = (a << (b & 0xF)) & 0xFFFF  # SLL
            elif prefix7 == 0b1111000:  self.regs[rd] = a >> (b & 0xF)              # SRL
            elif prefix7 == 0b1111001:                                               # SRA
                self.regs[rd] = (to_signed16(a) >> (b & 0xF)) & 0xFFFF

            return []

        # =================================================================
        # R,4 format (9-bit prefix): SLLI, SRLI, SRAI
        # =================================================================
        prefix9 = ir >> 7
        if prefix9 == 0b111101000:      # SLLI
            rd = ir & 7
            shamt = (ir >> 3) & 0xF
            self.regs[rd] = (self.regs[rd] << shamt) & 0xFFFF
            return []

        if prefix9 == 0b111101001:      # SRLI
            rd = ir & 7
            shamt = (ir >> 3) & 0xF
            self.regs[rd] = self.regs[rd] >> shamt
            return []

        if prefix9 == 0b111101010:      # SRAI
            rd = ir & 7
            shamt = (ir >> 3) & 0xF
            self.regs[rd] = (to_signed16(self.regs[rd]) >> shamt) & 0xFFFF
            return []

        # =================================================================
        # R,R format (10-bit prefix): LW.RR..SB.RR
        # =================================================================
        prefix10 = ir >> 6
        if prefix10 == 0b1111010110:    # LW.RR rd, rs
            rd = (ir >> 3) & 7
            rs = ir & 7
            addr = self.regs[rs]
            lo = self.ram[addr]
            hi = self.ram[(addr + 1) & 0xFFFF]
            self.regs[rd] = (hi << 8) | lo
            return [(addr, True, 0), ((addr + 1) & 0xFFFF, True, 0)]

        if prefix10 == 0b1111010111:    # LB.RR rd, rs
            rd = (ir >> 3) & 7
            addr = self.regs[ir & 7]
            byte = self.ram[addr]
            self.regs[rd] = (byte | 0xFF00) if byte & 0x80 else byte
            return [(addr, True, 0)]

        if prefix10 == 0b1111011000:    # LBU.RR rd, rs
            rd = (ir >> 3) & 7
            addr = self.regs[ir & 7]
            self.regs[rd] = self.ram[addr]
            return [(addr, True, 0)]

        if prefix10 == 0b1111011001:    # SW.RR rd, rs
            rd = (ir >> 3) & 7
            addr = self.regs[ir & 7]
            lo = self.regs[rd] & 0xFF
            hi = (self.regs[rd] >> 8) & 0xFF
            return [(addr, False, lo), ((addr + 1) & 0xFFFF, False, hi)]

        if prefix10 == 0b1111011010:    # SB.RR rd, rs
            rd = (ir >> 3) & 7
            addr = self.regs[ir & 7]
            return [(addr, False, self.regs[rd] & 0xFF)]

        # =================================================================
        # System format (10-bit prefix + sub)
        # =================================================================
        if prefix10 == 0b1111100000:
            sub = ir & 0x3F

            if sub == 0b000001:         # SEI
                self.i_bit = True
                return []

            if sub == 0b000010:         # CLI
                self.i_bit = False
                return []

            if sub == 0b000011:         # RETI
                self.i_bit = bool(self.epc & 1)
                self.pc = self.epc & 0xFFFE
                self._redirect = True
                return []

            if sub == 0b000101:         # WAI
                self.waiting = True
                return self._stale_addr(next_pc)

            if sub == 0b000111:         # STP
                self.stopped = True
                return self._stale_addr(next_pc)

            if (sub >> 3) == 0b010:     # EPCR rd
                self.regs[sub & 7] = self.epc
                return []

            if (sub >> 3) == 0b011:     # EPCW rs
                self.epc = self.regs[sub & 7]
                return []

            if sub & 0x20:              # INT/BRK (sub[5]=1)
                # BRK pc modification already done at dispatch
                vector_idx = ir & 3
                self.epc = self.pc      # pc has i_bit in bit 0
                self.pc = ((vector_idx + 1) & 3) << 1
                self._redirect = True
                return []

        # Unknown instruction — treat as 2-cycle NOP
        return []
