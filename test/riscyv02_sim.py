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
        self.t_bit = False      # T flag (condition result)
        self.esr = 0b10         # Exception status register: {I=1, T=0}
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
            # STP/WAI pipeline drain: the halt signal is generated BY the
            # cpu_clk domain (E_EXEC_LO → E_IDLE), so the fetch unit gets
            # one more negedge (F_LO → F_HI) before cpu_clk stops.
            # RDY=0 blocks the ICG immediately — no extra cycle.
            if (self.stopped or self.waiting) and self._bus_idx < len(self._bus_seq):
                self.current_sync = False
                addr, rwb, dout = self._bus_seq[self._bus_idx]
                self.current_addr = addr & 0xFFFF
                self.current_rwb = rwb
                self.current_dout = dout & 0xFF
                self._bus_idx += 1
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

        # INT detection: opcode 31 + ir[15:14]=11
        opcode = ir & 0x1F
        is_int = opcode == 31 and ((ir >> 14) & 3) == 3
        if is_int:
            self._saved_i_bit = self.i_bit  # Stash for EPC save in execute
            self.i_bit = True

        # Execute: applies architectural effects, returns memory bus entries.
        # Dispatch owns the fetch; execute only produces mem-phase entries.
        next_pc = self.pc
        self._redirect = False
        self._fast_redirect = False
        self._delayed_redirect = False  # INT/RETI: no insn_completing in RTL
        regs_before = list(self.regs)
        mem_entries = self._execute(ir)
        # Same-page redirect: 1 wasted fetch entry (3-cycle), else 2 (4-cycle)
        if self._fast_redirect:
            exec_entries = [(next_pc & 0xFFFF, True, 0)] + mem_entries
        else:
            exec_entries = self._fetch_seq(next_pc) + mem_entries

        # If a redirect occurred, append target fetch from new PC.
        if self._redirect:
            self._bus_seq = exec_entries + self._fetch_seq(self.pc)
        else:
            self._bus_seq = exec_entries
        # INT/RETI don't set insn_completing in RTL, so take_nmi can't fire
        # until E_IDLE (one cycle later). The +1 skips past the first target
        # fetch entry before becoming interruptible.
        if self._delayed_redirect:
            self._interrupt_point = len(exec_entries) + 1
        else:
            self._interrupt_point = len(exec_entries)
        self.current_sync = True

        self.last_dispatch = (
            f"INSN @0x{fetch_pc:04X} ir=0x{ir:04X}"
            f" regs={['%04X' % r for r in regs_before]}"
            f" -> pc=0x{self.pc:04X}"
            f" redir={self._redirect} seq={len(self._bus_seq)}"
        )

    def _take_interrupt(self, take_nmi):
        """Enter interrupt: save SR to ESR, save PC to EPC, jump to vector."""
        stale_pc = self.pc              # Always even (15-bit PC, bit 0 = 0)
        self.esr = (int(self.i_bit) << 1) | int(self.t_bit)
        self.i_bit = True

        if take_nmi:
            self.nmi_ack = True
            self.nmi_pending = True  # Cleared by nmi_ack next cycle
            vector = 0x0002
        else:
            vector = 0x0006

        self.epc = stale_pc             # Clean 16-bit return address
        self.pc = vector
        exec_entries = self._fetch_seq(stale_pc)  # Clean even address
        self._bus_seq = exec_entries + self._fetch_seq(vector)
        self._interrupt_point = len(exec_entries)
        self.last_dispatch = (
            f"{'NMI' if take_nmi else 'IRQ'}"
            f" epc=0x{stale_pc:04X} vec=0x{vector:04X}"
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

        opcode = ir & 0x1F

        # =================================================================
        # I-type (opcode 0-23)
        # =================================================================
        if opcode <= 23:
            rs_idx = (ir >> 5) & 7
            imm8_raw = (ir >> 8) & 0xFF

            if opcode == 0:             # ADDI
                self.regs[rs_idx] = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFF
                return []

            if opcode == 1:             # LI
                self.regs[rs_idx] = sext8(imm8_raw) & 0xFFFF
                return []

            if opcode == 2:             # LW (word load, base=R0)
                addr = (self.regs[0] + sext8(imm8_raw)) & 0xFFFF
                lo = self.ram[addr]
                hi = self.ram[(addr + 1) & 0xFFFF]
                self.regs[rs_idx] = (hi << 8) | lo
                return [(addr, True, 0),
                        ((addr + 1) & 0xFFFF, True, 0)]

            if opcode == 3:             # LB (sign-extend byte load, base=R0)
                addr = (self.regs[0] + sext8(imm8_raw)) & 0xFFFF
                byte = self.ram[addr]
                self.regs[rs_idx] = (byte | 0xFF00) if byte & 0x80 else byte
                return [(addr, True, 0)]

            if opcode == 4:             # LBU (zero-extend byte load, base=R0)
                addr = (self.regs[0] + sext8(imm8_raw)) & 0xFFFF
                self.regs[rs_idx] = self.ram[addr]
                return [(addr, True, 0)]

            if opcode == 5:             # SW (word store, base=R0)
                addr = (self.regs[0] + sext8(imm8_raw)) & 0xFFFF
                lo = self.regs[rs_idx] & 0xFF
                hi = (self.regs[rs_idx] >> 8) & 0xFF
                return [(addr, False, lo),
                        ((addr + 1) & 0xFFFF, False, hi)]

            if opcode == 6:             # SB (byte store, base=R0)
                addr = (self.regs[0] + sext8(imm8_raw)) & 0xFFFF
                return [(addr, False, self.regs[rs_idx] & 0xFF)]

            if opcode == 7:             # JR
                target = (self.regs[rs_idx] + sext8(imm8_raw)) & 0xFFFE
                self._fast_redirect = (self.regs[rs_idx] & 0xFF00) == (target & 0xFF00)
                self.pc = target
                self._redirect = True
                return []

            if opcode == 8:             # JALR (link to rs)
                old_rs = self.regs[rs_idx]
                self.regs[rs_idx] = next_pc
                self.pc = (old_rs + sext8(imm8_raw)) & 0xFFFE
                self._redirect = True
                return []

            if opcode == 9:             # ANDI (sign-extended imm)
                imm16 = sext8(imm8_raw) & 0xFFFF
                self.regs[rs_idx] = self.regs[rs_idx] & imm16
                return []

            if opcode == 10:            # ORI (sign-extended imm)
                imm16 = sext8(imm8_raw) & 0xFFFF
                self.regs[rs_idx] = self.regs[rs_idx] | imm16
                return []

            if opcode == 11:            # XORI (sign-extended imm)
                imm16 = sext8(imm8_raw) & 0xFFFF
                self.regs[rs_idx] = self.regs[rs_idx] ^ imm16
                return []

            if opcode == 12:            # CLTI (signed, sets T)
                self.t_bit = to_signed16(self.regs[rs_idx]) < sext8(imm8_raw)
                return []

            if opcode == 13:            # CLTUI (unsigned, sets T)
                self.t_bit = self.regs[rs_idx] < (sext8(imm8_raw) & 0xFFFF)
                return []

            if opcode == 14:            # BZ (×2 format: imm8 << 1)
                if self.regs[rs_idx] == 0:
                    target = (next_pc + sext8(imm8_raw) * 2) & 0xFFFF
                    self._fast_redirect = (next_pc & 0xFF00) == (target & 0xFF00)
                    self.pc = target
                    self._redirect = True
                return []

            if opcode == 15:            # BNZ (×2 format: imm8 << 1)
                if self.regs[rs_idx] != 0:
                    target = (next_pc + sext8(imm8_raw) * 2) & 0xFFFF
                    self._fast_redirect = (next_pc & 0xFF00) == (target & 0xFF00)
                    self.pc = target
                    self._redirect = True
                return []

            if opcode == 16:            # CEQI (sign-ext imm, equality, sets T)
                self.t_bit = self.regs[rs_idx] == (sext8(imm8_raw) & 0xFFFF)
                return []

            if opcode == 17:            # LWS (word load, base=R7)
                addr = (self.regs[7] + sext8(imm8_raw)) & 0xFFFF
                lo = self.ram[addr]
                hi = self.ram[(addr + 1) & 0xFFFF]
                self.regs[rs_idx] = (hi << 8) | lo
                return [(addr, True, 0), ((addr + 1) & 0xFFFF, True, 0)]

            if opcode == 18:            # LBS (sign-extend byte load, base=R7)
                addr = (self.regs[7] + sext8(imm8_raw)) & 0xFFFF
                byte = self.ram[addr]
                self.regs[rs_idx] = (byte | 0xFF00) if byte & 0x80 else byte
                return [(addr, True, 0)]

            if opcode == 19:            # LBUS (zero-extend byte load, base=R7)
                addr = (self.regs[7] + sext8(imm8_raw)) & 0xFFFF
                self.regs[rs_idx] = self.ram[addr]
                return [(addr, True, 0)]

            if opcode == 20:            # SWS (word store, base=R7)
                addr = (self.regs[7] + sext8(imm8_raw)) & 0xFFFF
                lo = self.regs[rs_idx] & 0xFF
                hi = (self.regs[rs_idx] >> 8) & 0xFF
                return [(addr, False, lo), ((addr + 1) & 0xFFFF, False, hi)]

            if opcode == 21:            # SBS (byte store, base=R7)
                addr = (self.regs[7] + sext8(imm8_raw)) & 0xFFFF
                return [(addr, False, self.regs[rs_idx] & 0xFF)]

            if opcode == 22:            # LUI (imm8 << 8)
                self.regs[rs_idx] = (imm8_raw << 8) & 0xFFFF
                return []

            if opcode == 23:            # AUIPC (pc + imm8 << 8)
                self.regs[rs_idx] = (next_pc + (imm8_raw << 8)) & 0xFFFF
                return []

            # Unknown I-type — treat as NOP
            return []

        # =================================================================
        # B-type (opcode 24): BT, BF
        # =================================================================
        if opcode == 24:
            funct3 = (ir >> 5) & 7
            imm8_raw = (ir >> 8) & 0xFF
            if funct3 == 0:             # BT
                if self.t_bit:
                    target = (next_pc + sext8(imm8_raw) * 2) & 0xFFFF
                    self._fast_redirect = (next_pc & 0xFF00) == (target & 0xFF00)
                    self.pc = target
                    self._redirect = True
                return []
            if funct3 == 1:             # BF
                if not self.t_bit:
                    target = (next_pc + sext8(imm8_raw) * 2) & 0xFFFF
                    self._fast_redirect = (next_pc & 0xFF00) == (target & 0xFF00)
                    self.pc = target
                    self._redirect = True
                return []
            return []

        # =================================================================
        # J-type (opcode 25): J, JAL
        # Immediate: {ir[15], ir[7:6], ir[14:8]} = {sign, imm[8:7], imm[6:0]}
        # =================================================================
        if opcode == 25:
            funct1 = (ir >> 5) & 1
            imm10 = (((ir >> 15) & 1) << 9) | (((ir >> 6) & 3) << 7) | ((ir >> 8) & 0x7F)
            if funct1 == 0:             # J
                target = (next_pc + sext10(imm10) * 2) & 0xFFFF
                # Same-page for small offset (high byte is pure sign extension)
                imm_bytes = sext10(imm10) * 2
                is_small = -256 <= imm_bytes <= 254
                self._fast_redirect = is_small and (next_pc & 0xFF00) == (target & 0xFF00)
                self.pc = target
                self._redirect = True
                return []
            if funct1 == 1:             # JAL (link to R6)
                self.regs[6] = next_pc
                self.pc = (next_pc + sext10(imm10) * 2) & 0xFFFF
                self._redirect = True
                return []
            return []

        # =================================================================
        # R-type (opcodes 26-29)
        # [fn2:2 @ 15:14][rd:3 @ 13:11][rs2:3 @ 10:8][rs1:3 @ 7:5]
        # =================================================================
        if 26 <= opcode <= 29:
            funct2 = (ir >> 14) & 3
            rd_idx = (ir >> 11) & 7
            rs2_idx = (ir >> 8) & 7
            rs1_idx = (ir >> 5) & 7
            a = self.regs[rs1_idx]
            b = self.regs[rs2_idx]

            if opcode == 26:            # R-ALU1: ADD, SUB, AND, OR
                if funct2 == 0:   self.regs[rd_idx] = (a + b) & 0xFFFF       # ADD
                elif funct2 == 1: self.regs[rd_idx] = (a - b) & 0xFFFF       # SUB
                elif funct2 == 2: self.regs[rd_idx] = a & b                   # AND
                elif funct2 == 3: self.regs[rd_idx] = a | b                   # OR
                return []

            if opcode == 27:            # R-ALU2: XOR, SLL, SRL, SRA
                if funct2 == 0:   self.regs[rd_idx] = a ^ b                   # XOR
                elif funct2 == 1: self.regs[rd_idx] = (a << (b & 0xF)) & 0xFFFF  # SLL
                elif funct2 == 2: self.regs[rd_idx] = a >> (b & 0xF)              # SRL
                elif funct2 == 3:                                                  # SRA
                    self.regs[rd_idx] = (to_signed16(a) >> (b & 0xF)) & 0xFFFF
                return []

            if opcode == 28:            # R-MEM: LWR, LBR, LBUR, SWR
                if funct2 == 0:         # LWR rd, rs1
                    addr = a
                    lo = self.ram[addr]
                    hi = self.ram[(addr + 1) & 0xFFFF]
                    self.regs[rd_idx] = (hi << 8) | lo
                    return [(addr, True, 0), ((addr + 1) & 0xFFFF, True, 0)]
                if funct2 == 1:         # LBR rd, rs1
                    addr = a
                    byte = self.ram[addr]
                    self.regs[rd_idx] = (byte | 0xFF00) if byte & 0x80 else byte
                    return [(addr, True, 0)]
                if funct2 == 2:         # LBUR rd, rs1
                    addr = a
                    self.regs[rd_idx] = self.ram[addr]
                    return [(addr, True, 0)]
                if funct2 == 3:         # SWR rs2, rs1  (data=rs2, addr=rs1)
                    addr = a
                    lo = b & 0xFF
                    hi = (b >> 8) & 0xFF
                    return [(addr, False, lo), ((addr + 1) & 0xFFFF, False, hi)]

            if opcode == 29:            # R-MISC: SBR, CLT, CLTU, CEQ
                if funct2 == 0:         # SBR rs2, rs1  (data=rs2, addr=rs1)
                    addr = a
                    return [(addr, False, b & 0xFF)]
                if funct2 == 1:         # CLT rs1, rs2
                    self.t_bit = to_signed16(a) < to_signed16(b)
                    return []
                if funct2 == 2:         # CLTU rs1, rs2
                    self.t_bit = a < b
                    return []
                if funct2 == 3:         # CEQ rs1, rs2
                    self.t_bit = a == b
                    return []

            return []

        # =================================================================
        # SI-type (opcode 30): SLLI, SRLI, SRAI
        # [fn2:2 @ 15:14][dc:2 @ 13:12][shamt:4 @ 11:8][rs/rd:3 @ 7:5]
        # =================================================================
        if opcode == 30:
            funct2 = (ir >> 14) & 3
            shamt = (ir >> 8) & 0xF
            rs_idx = (ir >> 5) & 7
            if funct2 == 0:             # SLLI
                self.regs[rs_idx] = (self.regs[rs_idx] << shamt) & 0xFFFF
                return []
            if funct2 == 1:             # SRLI
                self.regs[rs_idx] = self.regs[rs_idx] >> shamt
                return []
            if funct2 == 2:             # SRAI
                self.regs[rs_idx] = (to_signed16(self.regs[rs_idx]) >> shamt) & 0xFFFF
                return []
            return []

        # =================================================================
        # System (opcode 31)
        # [sub8:8 @ 15:8][reg:3 @ 7:5]
        # =================================================================
        if opcode == 31:
            sub8 = (ir >> 8) & 0xFF
            reg_idx = (ir >> 5) & 7

            if sub8 == 0x01:            # SEI
                self.i_bit = True
                return []

            if sub8 == 0x02:            # CLI
                self.i_bit = False
                return []

            if sub8 == 0x03:            # RETI
                self.i_bit = bool(self.esr & 2)
                self.t_bit = bool(self.esr & 1)
                self.pc = self.epc & 0xFFFE
                self._redirect = True
                self._delayed_redirect = True
                return []

            if sub8 == 0x05:            # WAI
                self.waiting = True
                return []

            if sub8 == 0x07:            # STP
                self.stopped = True
                return []

            if sub8 == 0x10:            # EPCR rd
                self.regs[reg_idx] = self.epc
                return []

            if sub8 == 0x18:            # EPCW rs
                self.epc = self.regs[reg_idx]
                return []

            if sub8 == 0x28:            # SRR rd
                self.regs[reg_idx] = (int(self.i_bit) << 1) | int(self.t_bit)
                return []

            if sub8 == 0x08:            # SRW rs
                val = self.regs[reg_idx]
                self.i_bit = bool(val & 2)
                self.t_bit = bool(val & 1)
                return []

            if (sub8 >> 6) == 3:        # INT (sub8[7:6]=11)
                vector_idx = (ir >> 6) & 3  # ir[7:6]
                self.esr = (int(self._saved_i_bit) << 1) | int(self.t_bit)
                self.epc = self.pc          # Clean return address
                self.pc = ((vector_idx + 1) & 3) << 1
                self._redirect = True
                self._delayed_redirect = True
                return []

        # Unknown instruction — treat as 2-cycle NOP
        return []
