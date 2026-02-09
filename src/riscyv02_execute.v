/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// ============================================================================
// Execute unit: FSM + ALU + register file.
//
// The register file lives here since only execute needs register access.
// Code is organized by state: combinational signals are computed in a single
// state-property block so each state's behavior is visible in one place.
//
// All instructions dispatch to E_EXEC_LO, then optionally continue to
// E_EXEC_HI (two-cycle ops). Memory instructions proceed from E_EXEC_HI
// to E_MEM_LO/HI for bus access.
//
// ISA encoding
// ------------
// Bits [15:12] form the "opcode" and determine the instruction format:
//
//   Opcode       Format   Description
//   0000..0011   U        Upper immediate (LUI, AUIPC; 3-bit prefix)
//   0100..0101   J        PC-relative jump (J, JAL)
//   0110..1010   S        Load/store (LB, LBU, LW, SB, SW; scrambled imm)
//   1011..1111   C        Compact (ALU, shift, branch, control, system)
//
// U-format uses a 3-bit prefix [15:13], gaining one extra immediate bit.
// All other formats use the full 4-bit opcode.
//
// Within C-format, bits [14:12] = group and [11:9] = sub identify the
// specific instruction, with op_r = {group, sub} read directly from the
// instruction word. S-format scrambles the immediate so rs1 stays at [5:3].
// ============================================================================

module riscyv02_execute (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        irqb,         // Interrupt request (active low, level-sensitive)
    input  wire        nmi_pending,  // NMI pending (from project.v, ungated domain)
    input  wire        nmi_edge,     // NMI combinational edge (same-cycle detection)
    input  wire        ir_valid,
    input  wire [15:0] fetch_ir,
    output reg         bus_active,
    output reg  [15:0] ab,
    output reg  [7:0]  dout,
    output reg         rwb,
    output wire        ir_accept,
    output reg         nmi_ack,      // NMI acknowledged (registered, for clearing nmi_pending)
    output wire        waiting,      // WAI: halted until interrupt (gates cpu_clk)
    output wire        stopped,      // STP: halted permanently, only reset recovers
    // Fetch pipeline flush and next-instruction address
    output reg         fetch_flush,
    output wire [15:0] fetch_pc
);

  // ==========================================================================
  // Interface and State
  // ==========================================================================

  // FSM states
  localparam E_IDLE    = 3'd0;  // Waiting for instruction
  localparam E_EXEC_LO = 3'd1;  // Execute / address compute low byte
  localparam E_EXEC_HI = 3'd2;  // Execute / address compute high byte
  localparam E_MEM_LO  = 3'd3;  // Memory access low byte
  localparam E_MEM_HI  = 3'd4;  // Memory access high byte (can accept next)

  reg [2:0]  state;
  reg [15:0] tmp;       // Cycle-to-cycle temporary (mem addr, branch target, shift carry)

  // Interrupt and PC state
  reg [15:0] pc;        // Program counter (next instruction to fetch; advanced at dispatch)
  reg [15:0] epc;       // Exception PC (bit 0 used for I flag on save)
  reg        i_bit;     // Interrupt disable flag (0=enabled, 1=disabled)

  // Decoded instruction state (latched at ir_accept)
  // op_r encoding: [5:3] = group, [2:0] = sub-opcode with meaningful bit properties
  // Group 000 — System (single-cycle, ISA grp 111 remapped here)
  localparam OP_NOP    = 6'b000_000;  // 0
  localparam OP_SEI    = 6'b000_001;  // 1
  localparam OP_CLI    = 6'b000_010;  // 2
  localparam OP_RETI   = 6'b000_011;  // 3
  localparam OP_BRK    = 6'b000_100;  // 4
  localparam OP_WAI    = 6'b000_101;  // 5
  localparam OP_STP    = 6'b000_111;  // 7
  // Group 001 — Memory (loads, stores, AUIPC)
  localparam OP_LW     = 6'b001_000;  // 8
  localparam OP_LB     = 6'b001_001;  // 9
  localparam OP_LBU    = 6'b001_010;  // 10
  localparam OP_AUIPC  = 6'b001_011;  // 11
  localparam OP_SW     = 6'b001_100;  // 12
  localparam OP_SB     = 6'b001_101;  // 13
  // Group 010 — Wide two-cycle (J, JAL, LUI, EPCR, EPCW)
  localparam OP_J      = 6'b010_000;  // 16
  localparam OP_JAL    = 6'b010_001;  // 17
  localparam OP_LUI    = 6'b010_100;  // 20
  localparam OP_EPCR   = 6'b010_101;  // 21
  localparam OP_EPCW   = 6'b010_110;  // 22
  // Group 011 — ALU RR (direct-mapped, sub = alu function)
  localparam OP_ADD    = 6'b011_000;  // 24
  localparam OP_SUB    = 6'b011_001;  // 25
  localparam OP_AND    = 6'b011_010;  // 26
  localparam OP_OR     = 6'b011_011;  // 27
  localparam OP_XOR    = 6'b011_100;  // 28
  localparam OP_SLT    = 6'b011_101;  // 29
  localparam OP_SLTU   = 6'b011_110;  // 30
  // Group 100 — Shift ([2]=immediate, [1]=right, [0]=arithmetic)
  localparam OP_SLL    = 6'b100_000;  // 32
  localparam OP_SRL    = 6'b100_010;  // 34
  localparam OP_SRA    = 6'b100_011;  // 35
  localparam OP_SLLI   = 6'b100_100;  // 36
  localparam OP_SRLI   = 6'b100_110;  // 38
  localparam OP_SRAI   = 6'b100_111;  // 39
  // Group 101 — Control (branches, LI, JR, JALR)
  localparam OP_BZ     = 6'b101_000;  // 40
  localparam OP_BNZ    = 6'b101_001;  // 41
  localparam OP_BLTZ   = 6'b101_010;  // 42
  localparam OP_BGEZ   = 6'b101_011;  // 43
  localparam OP_LI     = 6'b101_100;  // 44
  localparam OP_JR     = 6'b101_110;  // 46
  localparam OP_JALR   = 6'b101_111;  // 47
  // Group 110 — ALU Immediate (parallel sub-opcodes to group 011)
  localparam OP_ADDI   = 6'b110_000;  // 48
  localparam OP_ANDI   = 6'b110_010;  // 50
  localparam OP_ORI    = 6'b110_011;  // 51
  localparam OP_XORI   = 6'b110_100;  // 52
  localparam OP_SLTIF  = 6'b110_101;  // 53
  localparam OP_SLTIUF = 6'b110_110;  // 54
  localparam OP_XORIF  = 6'b110_111;  // 55

  localparam LINK_REG = 3'd6;  // R6 is the link register for JAL/JALR
  localparam T0_REG   = 3'd2;  // R2 is t0 for fixed-destination IF-type ops

  // Instruction format ranges (opcode = fetch_ir[15:12])
  //   U: 0000..0011   J: 0100..0101   S: 0110..1010   C: 1011..1111
  wire [3:0] opcode = fetch_ir[15:12];
  wire is_fmt_u = opcode[3:2] == 2'b00;                     // 0000..0011
  wire is_fmt_j = opcode[3:1] == 3'b010;                    // 0100..0101
  wire is_fmt_c = opcode >= 4'b1011;                         // 1011..1111

  reg [5:0]  op_r;            // Decoded instruction identity (group:3, sub:3)
  reg [3:0]  base_sel_r;      // Base register (or imm10[9:6] for AUIPC)
  reg [5:0]  off6_r;          // 6-bit offset
  reg [2:0]  rd_rs2_sel_r;    // Destination (LW) or source (SW)
  reg [2:0]  r_sel_r;         // Registered regfile read select (set at state transitions)
  reg [2:0]  r2_sel_r;        // Port 2 read select (set at dispatch, stable through execution)
  reg        r_hi_r;          // Registered regfile read hi/lo (set at state transitions)

  // ==========================================================================
  // Shared Infrastructure
  // ==========================================================================

  // -------------------------------------------------------------------------
  // Register file (8-bit interface)
  // -------------------------------------------------------------------------
  reg  [2:0] r_sel;
  reg        r_hi;
  wire [7:0] r;
  wire [7:0] r2;
  reg        w_hi;
  reg        w_we;
  reg  [7:0] w_data;

  wire [2:0] w_sel_mux = is_linking ? LINK_REG :
                         is_fixed_dest ? T0_REG : rd_rs2_sel_r;

  wire r2_hi_mux = is_shift ? 1'b0 : r_hi;

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .w_sel  (w_sel_mux),
    .w_hi   (w_hi),
    .w_data (w_data),
    .w_we   (w_we),
    .r_sel  (r_sel),
    .r_hi   (r_hi),
    .r      (r),
    .r2_sel (r2_sel_r),
    .r2_hi  (r2_hi_mux),
    .r2     (r2)
  );

  // -------------------------------------------------------------------------
  // ALU
  // -------------------------------------------------------------------------
  reg  [7:0] alu_a;
  reg  [7:0] alu_b;
  reg  [2:0] alu_op;
  reg        alu_new_op;
  wire [7:0] alu_result;
  wire       alu_co;

  riscyv02_alu u_alu (
    .clk    (clk),
    .rst_n  (rst_n),
    .a      (alu_a),
    .b      (alu_b),
    .op     (alu_op),
    .new_op (alu_new_op),
    .co     (alu_co),
    .result (alu_result)
  );

  // -------------------------------------------------------------------------
  // Barrel shifter
  // -------------------------------------------------------------------------
  wire [3:0] shamt = is_shift_rr ? r2[3:0] : off6_r[3:0];

  reg  [14:0] shifter_din;
  wire [7:0]  shifter_result;

  riscyv02_shifter u_shifter (
    .din    (shifter_din),
    .shamt  (shamt[2:0]),
    .result (shifter_result)
  );

  function [7:0] rev8(input [7:0] v);
    rev8 = {v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]};
  endfunction

  function [6:0] rev7(input [6:0] v);
    rev7 = {v[0], v[1], v[2], v[3], v[4], v[5], v[6]};
  endfunction

  // Group checks (one 3-bit compare each)
  wire is_mem_addr    = op_r[5:3] == 3'b001;           // Group 001
  wire is_alu_rr      = op_r[5:3] == 3'b011;           // Group 011
  wire is_alu_imm_grp = op_r[5:3] == 3'b110;           // Group 110
  wire is_shift       = op_r[5:3] == 3'b100;           // Group 100
  wire is_control     = op_r[5:3] == 3'b101;           // Group 101
  // Memory group properties
  wire is_store     = is_mem_addr && op_r[2] && !op_r[1];   // SW, SB
  wire is_byte_load = is_mem_addr && !op_r[2] && (op_r[1] ^ op_r[0]); // LB, LBU
  // ALU sub-groups (parallel encoding between groups 011 and 110)
  wire is_slt       = is_alu_rr      && op_r[2] && (op_r[1] ^ op_r[0]);
  wire is_slt_imm   = is_alu_imm_grp && op_r[2] && (op_r[1] ^ op_r[0]);
  wire is_alu_imm   = is_alu_imm_grp && !(op_r[2] && (op_r[1] ^ op_r[0]));
  wire is_fixed_dest = is_alu_imm_grp && op_r[2] && |op_r[1:0];
  // Shift properties — pure bit extraction
  wire is_shift_rr    = is_shift && !op_r[2];
  wire is_right_shift = is_shift && op_r[1];
  wire is_arith_shift = is_shift && op_r[0];
  // Control group properties
  wire is_branch      = is_control && !op_r[2];         // BZ,BNZ,BLTZ,BGEZ (sub 0xx)
  wire is_sign_branch = is_branch && op_r[1];           // BLTZ,BGEZ
  wire is_jr_jalr     = is_control && op_r[2] && op_r[1]; // JR(110),JALR(111)
  // Jump
  wire is_jump_imm    = op_r[5:3] == 3'b010 && !op_r[2]; // J(16),JAL(17)
  wire is_linking     = (is_jump_imm || is_jr_jalr) && op_r[0]; // JAL,JALR
  // Sub-opcode properties (named to avoid raw bit tests in behavioral code)
  wire is_byte_store = is_store && op_r[0];     // SB (vs SW)
  wire op_unsigned   = op_r[1]; // Unsigned variant: SLTU/SLTIUF/LBU (vs SLT/SLTIF/LB)
  wire branch_inv    = op_r[0]; // Branch inversion: BNZ/BGEZ invert condition
  wire is_two_cycle  = |op_r[5:3]; // Non-system group → needs E_EXEC_HI
  reg  nz_lo_r;  // Latched |rs_lo| for branch zero check

  // -------------------------------------------------------------------------
  // State-driven signals (computed in state-property block below)
  // -------------------------------------------------------------------------
  reg        insn_completing;
  reg [15:0] next_pc;   // Return address / resume point (pc for sequential, jump target for jumps)
  reg        jump;      // Whether next_pc isn't the sequential next instruction

  // Interrupt control: NMI has priority over IRQ.
  // nmi_edge is combinational so NMI is taken the same cycle the falling
  // edge arrives (no 1-cycle detection latency when fsm_ready).
  // nmi_ack guard prevents double-fire while waiting for project.v to clear
  // nmi_pending (nmi_ack stays high until the handshake completes).
  wire fsm_ready = state == E_IDLE || insn_completing;
  wire take_nmi = fsm_ready && (nmi_pending || nmi_edge) && !nmi_ack;
  wire take_irq = fsm_ready && !irqb && !i_bit && !take_nmi;
  assign ir_accept      = fsm_ready && ir_valid && !fetch_flush;
  assign waiting = (state == E_IDLE) && (op_r == OP_WAI);
  assign stopped = (state == E_IDLE) && (op_r == OP_STP);

  // ==========================================================================
  // State-Property Block
  //
  // All state-dependent combinational signals computed in one place.
  // Each state's properties are visible together.
  // ==========================================================================

  assign fetch_pc = pc;

  always @(*) begin
    // Defaults
    bus_active      = 1'b0;
    ab              = 16'bx;
    dout            = 8'bx;
    rwb             = 1'bx;
    alu_a           = r;
    alu_new_op      = 1'bx;
    alu_b           = 8'bx;
    alu_op          = 3'd0;    // ADD (safe default for address computation)
    r_sel           = r_sel_r;
    r_hi            = r_hi_r;
    w_hi            = 1'bx;
    w_data          = uio_in;
    w_we            = 1'b0;
    insn_completing = 1'b0;
    next_pc         = pc;
    jump            = 1'b0;
    shifter_din     = 15'b0;

    // ALU operation select: groups 011/110 share sub-opcode semantics
    if (is_alu_rr || is_alu_imm_grp) begin
      if (is_slt || is_slt_imm)
        alu_op = 3'd1;                // SLT variants → SUB
      else if (!op_r[2])
        alu_op = op_r[2:0];           // ADD=0, SUB=1, AND=2, OR=3
      else
        alu_op = 3'd4;                // XOR, XORI, XORIF
    end

    case (state)
      E_IDLE: ;

      E_EXEC_LO: begin
        if (op_r == OP_RETI) begin
          insn_completing = 1'b1;
          jump      = 1'b1;
          next_pc   = {epc[15:1], 1'b0};
        end else if (op_r == OP_EPCR) begin
          w_data = epc[7:0];
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (op_r == OP_EPCW) begin
        end else if (is_mem_addr) begin
          // Address computation low byte (loads, stores, AUIPC)
          alu_new_op = 1'b1;
          if (op_r == OP_AUIPC) begin
            alu_a  = pc[7:0];
            alu_b  = {off6_r[1:0], 6'b0};            // (imm10 << 6) low byte
            w_data = alu_result;
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end else
            alu_b = {{2{off6_r[5]}}, off6_r};        // unscaled byte offset
        end else if (is_jr_jalr) begin
          // JR/JALR address computation low byte
          alu_a      = r;
          alu_b      = {off6_r[5], off6_r, 1'b0};     // offset * 2 (code alignment)
          alu_new_op = 1'b1;
          if (is_linking) begin                        // JALR
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else if (is_alu_rr) begin
          alu_b      = r2;
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
          if (is_slt) begin
            w_data = 8'h00;
            w_hi   = 1'b1;
          end
        end else if (is_alu_imm) begin
          alu_b      = {{2{off6_r[5]}}, off6_r};
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_slt_imm) begin
          alu_b      = {{2{off6_r[5]}}, off6_r};
          alu_new_op = 1'b1;
          w_data     = 8'h00;
          w_hi       = 1'b1;
          w_we       = 1'b1;
        end else if (is_shift) begin
          // Cycle 1: left shifts process lo normally, right shifts process hi
          // (r_hi_r=1 from dispatch for right shifts, so r = hi byte).
          if (shamt[3]) begin
            // Cross-byte: entire result comes from the other byte.
            // Write zero (or sign for SRA) for the evacuated byte.
            w_data = is_right_shift ?
                     (is_arith_shift ? {8{r[7]}} : 8'h00) : 8'h00;
            w_hi   = is_right_shift ? 1'b1 : 1'b0;
            w_we   = 1'b1;
          end else if (is_right_shift) begin
            // Right shift hi byte: fill from sign/zero
            shifter_din = {is_arith_shift ? {7{r[7]}} : 7'b0, r};
            w_data = shifter_result;
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else begin
            // Left shift lo byte: reverse, right-shift, reverse
            shifter_din = {7'b0, rev8(r)};
            w_data = rev8(shifter_result);
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else if (op_r == OP_LI) begin
          w_data = {{2{off6_r[5]}}, off6_r};
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (op_r == OP_LUI) begin
          w_data = {off6_r[1:0], 6'b0};
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_branch) begin
          alu_a      = pc[7:0];
          alu_b      = {off6_r[5], off6_r, 1'b0};
          alu_new_op = 1'b1;
        end else if (is_jump_imm) begin
          alu_a      = pc[7:0];
          alu_b      = {off6_r[3:0], rd_rs2_sel_r, 1'b0};
          alu_new_op = 1'b1;
          if (is_linking) begin                          // JAL
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else begin
          if (op_r == OP_BRK)
            jump = 1'b1;
          else if (op_r != OP_WAI && op_r != OP_STP)
            insn_completing = 1'b1;
        end
      end

      E_EXEC_HI: begin
        if (is_mem_addr) begin
          // Address computation high byte (loads, stores, AUIPC)
          alu_new_op = 1'b0;
          if (op_r == OP_AUIPC) begin
            alu_a           = pc[15:8];
            alu_b           = {base_sel_r, off6_r[5:2]};   // (imm10 << 6) high byte
            w_data          = alu_result;
            w_hi            = 1'b1;
            w_we            = 1'b1;
            insn_completing = 1'b1;
          end else
            alu_b = {8{off6_r[5]}};  // sign extension
        end else if (is_jr_jalr) begin
          // JR/JALR address computation high byte
          alu_b           = {8{off6_r[5]}};
          alu_new_op      = 1'b0;
          jump            = 1'b1;
          next_pc         = {alu_result, tmp[7:0]};
          insn_completing = 1'b1;
          if (is_linking) begin                          // JALR
            w_data = pc[15:8];
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end
        end else begin
          // Execute high byte (ALU, shift, branch, jump, LI, LUI, EPC)
          insn_completing = 1'b1;
          if (op_r == OP_EPCR) begin
            w_data = epc[15:8];
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (is_alu_rr) begin
            alu_b      = r2;
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
            if (is_slt) begin
              w_hi = 1'b0;
              if (op_unsigned)                           // SLTU
                w_data = {7'b0, ~alu_co};
              else
                w_data = {7'b0, (r[7] ^ r2[7]) ? r[7] : alu_result[7]};
            end
          end else if (is_alu_imm) begin
            alu_b      = {8{off6_r[5]}};
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
          end else if (is_slt_imm) begin
            alu_b      = {8{off6_r[5]}};
            alu_new_op = 1'b0;
            w_hi       = 1'b0;
            w_we       = 1'b1;
            if (op_unsigned)                              // SLTIUF
              w_data = {7'b0, ~alu_co};
            else
              w_data = {7'b0, (r[7] ^ off6_r[5]) ? r[7] : alu_result[7]};
          end else if (is_shift) begin
            // Cycle 2: left shifts process hi, right shifts process lo.
            if (shamt[3]) begin
              // Cross-byte: use tmp[7:0] as data (it has the other byte).
              if (is_right_shift) begin
                shifter_din = {is_arith_shift ? {7{tmp[7]}} : 7'b0, tmp[7:0]};
                w_data = shifter_result;
                w_hi   = 1'b0;
                w_we   = 1'b1;
              end else begin
                shifter_din = {7'b0, rev8(tmp[7:0])};
                w_data = rev8(shifter_result);
                w_hi   = 1'b1;
                w_we   = 1'b1;
              end
            end else if (is_right_shift) begin
              // Right shift lo byte: fill from tmp low bits
              shifter_din = {tmp[6:0], r};
              w_data = shifter_result;
              w_hi   = 1'b0;
              w_we   = 1'b1;
            end else begin
              // Left shift hi byte: reverse, right-shift with reversed tmp fill, reverse
              shifter_din = {rev7(tmp[7:1]), rev8(r)};
              w_data = rev8(shifter_result);
              w_hi   = 1'b1;
              w_we   = 1'b1;
            end
          end else if (op_r == OP_LI) begin
            w_data = {8{off6_r[5]}};
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (op_r == OP_LUI) begin
            w_data = {base_sel_r, off6_r[5:2]};
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (is_branch) begin
            alu_a      = pc[15:8];
            alu_b      = {8{off6_r[5]}};
            alu_new_op = 1'b0;
            if ((is_sign_branch ? r[7] : !nz_lo_r && r == 8'h00) ^ branch_inv) begin
              jump    = 1'b1;
              next_pc = {alu_result, tmp[7:0]};
            end
          end else if (is_jump_imm) begin
            alu_a      = pc[15:8];
            alu_b      = {{3{base_sel_r[2]}}, base_sel_r[2:0], off6_r[5:4]};
            alu_new_op = 1'b0;
            jump       = 1'b1;
            next_pc    = {alu_result, tmp[7:0]};
            if (is_linking) begin                              // JAL
              w_data = pc[15:8];
              w_hi   = 1'b1;
              w_we   = 1'b1;
            end
          end
        end
      end

      E_MEM_LO: begin
        bus_active   = 1'b1;
        ab           = tmp;
        w_hi         = 1'b0;
        w_we         = !is_store;
        if (is_byte_store)                              // SB
          insn_completing = 1'b1;
      end

      E_MEM_HI: begin
        insn_completing = 1'b1;
        w_hi            = 1'b1;
        if (is_byte_load) begin
          w_data        = op_unsigned ? 8'h00 : {8{r[7]}}; // LBU : LB
          w_we          = 1'b1;
        end else begin
          bus_active    = 1'b1;
          ab            = {tmp[15:8] + {7'b0, ~|tmp[7:0]}, tmp[7:0]};
          w_we          = !is_store;
        end
      end

    endcase

    case (state)
      E_MEM_LO, E_MEM_HI: begin
        dout = r2;  // rs2 via port 2 (low-fanout path to uio_out)
        rwb  = !is_store;
      end
    endcase

    // Flush: interrupts or instruction jump (JR, RETI, BRK).
    fetch_flush = take_nmi || take_irq || jump;
  end

  // ==========================================================================
  // FSM (negedge clk)
  // ==========================================================================

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state            <= E_IDLE;
      tmp              <= 16'h0000;
      op_r             <= OP_NOP;
      base_sel_r       <= 4'b0000;
      off6_r           <= 6'b000000;
      rd_rs2_sel_r     <= 3'b000;
      r_sel_r          <= 3'b000;
      r2_sel_r         <= 3'b000;
      r_hi_r           <= 1'b0;
      nz_lo_r          <= 1'b0;
      pc               <= 16'h0000;
      epc              <= 16'h0000;
      i_bit            <= 1'b1;  // Interrupts disabled after reset
      nmi_ack          <= 1'b0;
    end else begin
      // NMI handshake: set nmi_ack when NMI is taken; hold until project.v
      // clears nmi_pending, then release.
      if (!nmi_pending) nmi_ack <= 1'b0;
      else if (take_nmi) nmi_ack <= 1'b1;

      // ---------------------------------------------------------------------
      // State machine: transitions and per-state effects.
      // Ordered before interrupt entry and dispatch so their overrides
      // (state, epc, i_bit, pc, op_r) take priority via last-NBA-wins.
      // ---------------------------------------------------------------------
      case (state)
        E_IDLE: ;

        E_EXEC_LO: begin
          if (op_r == OP_SEI) i_bit <= 1'b1;
          if (op_r == OP_CLI) i_bit <= 1'b0;
          if (op_r == OP_RETI) begin
            i_bit <= epc[0];
            pc    <= next_pc;
          end
          if (op_r == OP_EPCW) epc[7:0] <= r;
          if (op_r == OP_BRK) begin
            epc   <= next_pc | {15'b0, i_bit};
            i_bit <= 1'b1;
            pc    <= 16'h000C;
          end
          if (is_branch || is_jump_imm || is_mem_addr || is_jr_jalr)
            tmp[7:0] <= alu_result;
          if (is_branch) nz_lo_r <= |r;
          if (is_shift) tmp[7:0] <= r;
          if (is_two_cycle) begin
            state  <= E_EXEC_HI;
            r_hi_r <= is_right_shift ? 1'b0 : 1'b1;
          end else
            state <= E_IDLE;
        end

        E_EXEC_HI: begin
          if (is_mem_addr) begin
            // Address high byte computed; set up for memory access or complete.
            tmp[15:8]   <= alu_result;
            r_sel_r     <= rd_rs2_sel_r;
            r_hi_r      <= 1'b0;
            state       <= (op_r == OP_AUIPC) ? E_IDLE : E_MEM_LO;
          end else if (is_jr_jalr) begin
            pc    <= next_pc;
            state <= E_IDLE;
          end else begin
            if (op_r == OP_EPCW) epc[15:8] <= r;
            if (jump) pc <= next_pc;
            state <= E_IDLE;
          end
        end

        E_MEM_LO: begin
          r_hi_r    <= is_byte_load ? 1'b0 : 1'b1;
          tmp[7:0]  <= tmp[7:0] + 8'd1;  // Increment for E_MEM_HI address
          state     <= is_byte_store ? E_IDLE : E_MEM_HI;
        end

        E_MEM_HI: state <= E_IDLE;

        default: state <= 3'bx;
      endcase

      // ---------------------------------------------------------------------
      // Interrupt entry (overrides state, epc, i_bit from case block)
      // ---------------------------------------------------------------------
      if (take_nmi || take_irq) begin
        epc   <= next_pc | {15'b0, i_bit};
        i_bit <= 1'b1;
        op_r  <= OP_NOP;
        state <= E_IDLE;
        if (take_nmi) pc <= 16'h0008;
        else          pc <= 16'h0004;
      end

      // ---------------------------------------------------------------------
      // Instruction dispatch (overrides state, op_r from case block).
      // ir_accept fires in any completing state (insn_completing=1) or
      // E_IDLE, as long as ir_valid && !fetch_flush.
      //
      // The opcode (bits [15:12]) determines the instruction format:
      //   U (0000..0011)  — op_r from prefix, base_sel from imm10[9:6]
      //   J (0100..0101)  — op_r from prefix, base_sel from off12[11:9]
      //   S (0110..1010)  — op_r from prefix, off6 from scrambled imm
      //   C (1011..1111)  — op_r = {group, sub} direct from [14:9]
      // ---------------------------------------------------------------------
      if (ir_accept) begin
        pc <= pc + 16'd2;

        // --- off6_r default (C/I-type): imm6 at [8:3] ---
        off6_r[5:3] <= fetch_ir[8:6];
        off6_r[2:0] <= fetch_ir[5:3];

        // --- r2_sel_r default: rs2 at [8:6] (R-type, shifts) ---
        r2_sel_r <= fetch_ir[8:6];

        // --- Decode op_r from opcode ---
        // U-format: 3-bit prefix determines instruction
        if      (fetch_ir[15:13] == 3'b000)  op_r <= OP_LUI;
        else if (fetch_ir[15:13] == 3'b001)  op_r <= OP_AUIPC;
        // J-format: 4-bit opcode determines instruction
        else if (opcode == 4'b0100)          op_r <= OP_J;
        else if (opcode == 4'b0101)          op_r <= OP_JAL;
        // S-format: 4-bit opcode determines instruction; off6 from scrambled imm
        else if (opcode == 4'b0110) begin
          op_r <= OP_LB;
          off6_r[2:0] <= fetch_ir[11:9];
        end
        else if (opcode == 4'b0111) begin
          op_r <= OP_LBU;
          off6_r[2:0] <= fetch_ir[11:9];
        end
        else if (opcode == 4'b1000) begin
          op_r <= OP_LW;
          off6_r[2:0] <= fetch_ir[11:9];
        end
        else if (opcode == 4'b1001) begin
          op_r <= OP_SB;
          off6_r[2:0] <= fetch_ir[11:9];
          r2_sel_r <= fetch_ir[2:0];
        end
        else if (opcode == 4'b1010) begin
          op_r <= OP_SW;
          off6_r[2:0] <= fetch_ir[11:9];
          r2_sel_r <= fetch_ir[2:0];
        end
        // C-format: direct-mapped, with two remapping exceptions
        else if (fetch_ir[14:12] == 3'b111) begin
          // System group: EPCR remapped to wide group, rest to group 000
          if (fetch_ir[11:9] == 3'b110)
            op_r <= OP_EPCR;
          else
            op_r <= {3'b000, fetch_ir[11:9]};
        end
        else if (fetch_ir[14:9] == 6'b101_101)
          op_r <= OP_EPCW;                       // EPCW remap
        else if (fetch_ir[15])
          op_r <= fetch_ir[14:9];                // direct: op_r = {group, sub}
        else
          op_r <= OP_NOP;

        // --- base_sel_r: format-dependent upper bits ---
        if (is_fmt_u)
          base_sel_r <= fetch_ir[12:9];          // U: imm10[9:6]
        else if (is_fmt_j)
          base_sel_r <= {1'b0, fetch_ir[11:9]};  // J: off12[11:9]
        else
          base_sel_r <= {1'b0, fetch_ir[5:3]};   // S/C: don't-care

        rd_rs2_sel_r <= fetch_ir[2:0];

        // --- r_sel_r: format-dependent register select ---
        // C-format I-type: rs/rd at [2:0]. R-type: rs1 at [5:3].
        // S/U/J formats: rs1 at [5:3] (or don't-care).
        if (is_fmt_c &&
            fetch_ir[14:12] != 3'b011 &&
            !(fetch_ir[14:12] == 3'b100 && !fetch_ir[11]))
          r_sel_r <= fetch_ir[2:0];   // C-format I-type
        else
          r_sel_r <= fetch_ir[5:3];   // C-format R-type, S, U, J

        // --- r_hi_r: right shifts read hi byte first ---
        r_hi_r <= (is_fmt_c && fetch_ir[14:12] == 3'b100 && fetch_ir[10])
                  ? 1'b1 : 1'b0;

        // All instructions start in E_EXEC_LO
        state <= E_EXEC_LO;
      end
    end
  end

endmodule
