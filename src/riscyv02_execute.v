/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// ============================================================================
// Execute unit: FSM + ALU + register file.
//
// All instruction state is held in a single 16-bit ir register containing the
// raw instruction word (or a synthesized pseudo-instruction for interrupts).
// Instruction identity (op) and fields (off6, register selects) are derived
// combinationally from ir, eliminating registered decode state.
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
//   0110..1010   S        Load/store (LB, LBU, LW, SB, SW; rs1 at [11:9])
//   1011..1111   C        Compact (ALU, shift, branch, control, system)
//
// U-format uses a 3-bit prefix [15:13], gaining one extra immediate bit.
// All other formats use the full 4-bit opcode.
//
// Within C-format, bits [14:12] = group and [11:9] = sub identify the
// specific instruction, with op = {group, sub} read directly from the
// instruction word. S-format places rs1 at [11:9], off6 at [8:3].
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
  reg [15:0] ir;        // Instruction register (raw or synthesized for interrupts)
  reg [15:0] tmp;       // Cycle-to-cycle temporary (mem addr, branch target, shift carry)

  // Interrupt and PC state
  reg [15:0] pc;        // Program counter (next instruction to fetch; advanced at dispatch)
  reg        i_bit;     // Interrupt disable flag (0=enabled, 1=disabled)

  // -------------------------------------------------------------------------
  // Combinational instruction decode from ir
  // -------------------------------------------------------------------------
  // op encoding: [5:3] = group, [2:0] = sub-opcode with meaningful bit properties
  // Group 000 — System (single-cycle, ISA grp 111 remapped here)
  localparam OP_NOP    = 6'b000_000;  // 0
  localparam OP_SEI    = 6'b000_001;  // 1
  localparam OP_CLI    = 6'b000_010;  // 2
  localparam OP_RETI   = 6'b000_011;  // 3
  localparam OP_WAI    = 6'b000_101;  // 5
  localparam OP_INT = 6'b000_110; // 6 — software/hardware interrupt
  localparam OP_STP    = 6'b000_111;  // 7
  // Group 001 — Memory (loads, stores, AUIPC)
  localparam OP_LW     = 6'b001_000;  // 8
  localparam OP_LB     = 6'b001_001;  // 9
  localparam OP_LBU    = 6'b001_010;  // 10
  localparam OP_AUIPC  = 6'b001_011;  // 11
  localparam OP_SW     = 6'b001_100;  // 12
  localparam OP_SB     = 6'b001_101;  // 13
  // Group 010 — Wide two-cycle (J, JAL, LUI)
  localparam OP_J      = 6'b010_000;  // 16
  localparam OP_JAL    = 6'b010_001;  // 17
  localparam OP_LUI    = 6'b010_100;  // 20
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

  // Combinational op decode: maps ir to internal group:sub encoding
  reg [5:0] op;
  always @(*) begin
    if      (ir[15:13] == 3'b000)                          op = OP_LUI;
    else if (ir[15:13] == 3'b001)                          op = OP_AUIPC;
    else if (ir[15:12] == 4'b0100)                         op = OP_J;
    else if (ir[15:12] == 4'b0101)                         op = OP_JAL;
    else if (ir[15:12] == 4'b0110)                         op = OP_LB;
    else if (ir[15:12] == 4'b0111)                         op = OP_LBU;
    else if (ir[15:12] == 4'b1000)                         op = OP_LW;
    else if (ir[15:12] == 4'b1001)                         op = OP_SB;
    else if (ir[15:12] == 4'b1010)                         op = OP_SW;
    else if (ir[14:12] == 3'b111 && ir[11:9] == 3'b100)   op = OP_INT;
    else if (ir[14:12] == 3'b111)                          op = {3'b000, ir[11:9]};
    else if (ir[15])                                       op = ir[14:9];
    else                                                   op = OP_NOP;
  end

  // Instruction fields derived directly from ir
  wire [5:0] off6 = ir[8:3];

  // Format detection from ir (used for r_sel derivation)
  wire ir_fmt_u = ir[15:14] == 2'b00;
  wire ir_fmt_j = ir[15:13] == 3'b010;
  wire ir_fmt_c = ir[15] && (ir[14] || (ir[13] && ir[12]));

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
                         is_fixed_dest ? T0_REG : ir[2:0];

  wire [2:0] r2_sel = is_store ? ir[2:0] : ir[8:6];
  wire r2_hi_mux = is_shift ? 1'b0 : r_hi;

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .i_bit  (i_bit),
    .w_sel  (w_sel_mux),
    .w_hi   (w_hi),
    .w_data (w_data),
    .w_we   (w_we),
    .r_sel  (r_sel),
    .r_hi   (r_hi),
    .r      (r),
    .r2_sel (r2_sel),
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
  wire [3:0] shamt = is_shift_rr ? r2[3:0] : off6[3:0];

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
  wire is_mem_addr    = op[5:3] == 3'b001;           // Group 001
  wire is_alu_rr      = op[5:3] == 3'b011;           // Group 011
  wire is_alu_imm_grp = op[5:3] == 3'b110;           // Group 110
  wire is_shift       = op[5:3] == 3'b100;           // Group 100
  wire is_control     = op[5:3] == 3'b101;           // Group 101
  // Memory group properties
  wire is_store     = is_mem_addr && op[2] && !op[1];   // SW, SB
  wire is_byte_load = is_mem_addr && !op[2] && (op[1] ^ op[0]); // LB, LBU
  // ALU sub-groups (parallel encoding between groups 011 and 110)
  wire is_slt       = is_alu_rr      && op[2] && (op[1] ^ op[0]);
  wire is_slt_imm   = is_alu_imm_grp && op[2] && (op[1] ^ op[0]);
  wire is_alu_imm   = is_alu_imm_grp && !(op[2] && (op[1] ^ op[0]));
  wire is_fixed_dest = is_alu_imm_grp && op[2] && |op[1:0];
  // Shift properties — pure bit extraction
  wire is_shift_rr    = is_shift && !op[2];
  wire is_right_shift = is_shift && op[1];
  wire is_arith_shift = is_shift && op[0];
  // Control group properties
  wire is_branch      = is_control && !op[2];         // BZ,BNZ,BLTZ,BGEZ (sub 0xx)
  wire is_sign_branch = is_branch && op[1];           // BLTZ,BGEZ
  wire is_jr_jalr     = is_control && op[2] && op[1]; // JR(110),JALR(111)
  // Jump
  wire is_jump_imm    = op[5:3] == 3'b010 && !op[2]; // J(16),JAL(17)
  wire is_linking     = (is_jump_imm || is_jr_jalr) && op[0]; // JAL,JALR
  // Sub-opcode properties (named to avoid raw bit tests in behavioral code)
  wire is_byte_store = is_store && op[0];     // SB (vs SW)
  wire op_unsigned   = op[1]; // Unsigned variant: SLTU/SLTIUF/LBU (vs SLT/SLTIF/LB)
  wire branch_inv    = op[0]; // Branch inversion: BNZ/BGEZ invert condition
  wire is_int   = (op == OP_INT); // INT instruction (BRK / hw interrupt)
  wire is_two_cycle  = |op[5:3]; // Non-system group → needs E_EXEC_HI

  // -------------------------------------------------------------------------
  // Combinational register-file select from (state, ir, op)
  // -------------------------------------------------------------------------

  // r_sel: read port 1 register select
  //   E_MEM states: read rd/rs2 at ir[2:0]
  //   Execute states: format-dependent source register
  always @(*) begin
    if (state == E_MEM_LO || state == E_MEM_HI)
      r_sel = ir[2:0];
    else if (op == OP_RETI)
      r_sel = 3'd6;                                 // Banked R6
    else if (ir_fmt_c &&
             ir[14:12] != 3'b011 &&
             !(ir[14:12] == 3'b100 && !ir[11]))
      r_sel = ir[2:0];                              // C I-type: rd/rs at [2:0]
    else if (ir_fmt_c || ir_fmt_u || ir_fmt_j)
      r_sel = ir[5:3];                              // C R-type, U, J: rs1 at [5:3]
    else
      r_sel = ir[11:9];                             // S-format: rs1 at [11:9]
  end

  // r_hi: read port 1 byte select
  //   E_EXEC_LO: high byte first for right shifts, low byte for everything else
  //   E_EXEC_HI: swapped (low for right shifts, high for everything else)
  //   E_MEM_LO: low byte
  //   E_MEM_HI: high byte (except byte loads re-read low for sign extension)
  always @(*) begin
    case (state)
      E_EXEC_LO: r_hi = is_right_shift;
      E_EXEC_HI: r_hi = !is_right_shift;
      E_MEM_LO:  r_hi = 1'b0;
      E_MEM_HI:  r_hi = !is_byte_load;
      default:   r_hi = 1'b0;
    endcase
  end

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
  assign waiting = (state == E_IDLE) && (op == OP_WAI);
  assign stopped = (state == E_IDLE) && (op == OP_STP);

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
      else if (!op[2])
        alu_op = op[2:0];           // ADD=0, SUB=1, AND=2, OR=3
      else
        alu_op = 3'd4;                // XOR, XORI, XORIF
    end

    case (state)
      E_IDLE: ;

      E_EXEC_LO: begin
        if (op == OP_RETI) begin
          // Read banked R6 low byte (r_sel=6, r_hi=0, i_bit=1)
          // Captured into tmp[7:0] at negedge
        end else if (is_int) begin
          w_data = pc[7:0];          // Return addr low byte (i_bit in bit 0)
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_mem_addr) begin
          // Address computation low byte (loads, stores, AUIPC)
          alu_new_op = 1'b1;
          if (op == OP_AUIPC) begin
            alu_a  = pc[7:0];
            alu_b  = {off6[1:0], 6'b0};             // (imm10 << 6) low byte
            w_data = alu_result;
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end else
            alu_b = {{2{off6[5]}}, off6};            // unscaled byte offset
        end else if (is_jr_jalr) begin
          // JR/JALR address computation low byte
          alu_a      = r;
          alu_b      = {off6[5], off6, 1'b0};        // offset * 2 (code alignment)
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
          alu_b      = {{2{off6[5]}}, off6};
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_slt_imm) begin
          alu_b      = {{2{off6[5]}}, off6};
          alu_new_op = 1'b1;
          w_data     = 8'h00;
          w_hi       = 1'b1;
          w_we       = 1'b1;
        end else if (is_shift) begin
          // Cycle 1: left shifts process lo normally, right shifts process hi
          // (r_hi=1 from combinational decode for right shifts, so r = hi byte).
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
        end else if (op == OP_LI) begin
          w_data = {{2{off6[5]}}, off6};
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (op == OP_LUI) begin
          w_data = {off6[1:0], 6'b0};
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_branch) begin
          alu_a      = pc[7:0];
          alu_b      = {off6[5], off6, 1'b0};
          alu_new_op = 1'b1;
        end else if (is_jump_imm) begin
          alu_a      = pc[7:0];
          alu_b      = {ir[6:0], 1'b0};              // off12[6:0] << 1
          alu_new_op = 1'b1;
          if (is_linking) begin                          // JAL
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else begin
          if (op != OP_WAI && op != OP_STP)
            insn_completing = 1'b1;
        end
      end

      E_EXEC_HI: begin
        if (is_mem_addr) begin
          // Address computation high byte (loads, stores, AUIPC)
          alu_new_op = 1'b0;
          if (op == OP_AUIPC) begin
            alu_a           = pc[15:8];
            alu_b           = ir[12:5];              // (imm10 << 6) high byte
            w_data          = alu_result;
            w_hi            = 1'b1;
            w_we            = 1'b1;
            insn_completing = 1'b1;
          end else
            alu_b = {8{off6[5]}};  // sign extension
        end else if (is_jr_jalr) begin
          // JR/JALR address computation high byte
          alu_b           = {8{off6[5]}};
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
          if (op == OP_RETI) begin
            // Hi byte from current read (r_sel=6, r_hi=1), lo from tmp
            jump    = 1'b1;
            next_pc = {r, tmp[7:1], 1'b0};
          end else if (is_int) begin
            w_data  = pc[15:8];
            w_hi    = 1'b1;
            w_we    = 1'b1;
            jump    = 1'b1;
            next_pc = {12'b0, off6[1:0] + 2'd1, 2'b00};  // Vector address (skip reset at 0)
          end else begin
          // Execute high byte (ALU, shift, branch, jump, LI, LUI)
          insn_completing = 1'b1;
          if (is_alu_rr) begin
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
            alu_b      = {8{off6[5]}};
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
          end else if (is_slt_imm) begin
            alu_b      = {8{off6[5]}};
            alu_new_op = 1'b0;
            w_hi       = 1'b0;
            w_we       = 1'b1;
            if (op_unsigned)                              // SLTIUF
              w_data = {7'b0, ~alu_co};
            else
              w_data = {7'b0, (r[7] ^ off6[5]) ? r[7] : alu_result[7]};
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
          end else if (op == OP_LI) begin
            w_data = {8{off6[5]}};
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (op == OP_LUI) begin
            w_data = ir[12:5];                         // {imm10[9:6], imm10[5:2]}
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (is_branch) begin
            alu_a      = pc[15:8];
            alu_b      = {8{off6[5]}};
            alu_new_op = 1'b0;
            if ((is_sign_branch ? r[7] : !tmp[8] && r == 8'h00) ^ branch_inv) begin
              jump    = 1'b1;
              next_pc = {alu_result, tmp[7:0]};
            end
          end else if (is_jump_imm) begin
            alu_a      = pc[15:8];
            alu_b      = {{3{ir[11]}}, ir[11:7]};     // sext(off12[11:7])
            alu_new_op = 1'b0;
            jump       = 1'b1;
            next_pc    = {alu_result, tmp[7:0]};
            if (is_linking) begin                              // JAL
              w_data = pc[15:8];
              w_hi   = 1'b1;
              w_we   = 1'b1;
            end
          end
          end // inner else (insn_completing path)
        end // outer else (not mem, not jr_jalr)
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
        bus_active      = !is_byte_load;
        ab              = {tmp[15:8] + {7'b0, ~|tmp[7:0]}, tmp[7:0]};
        if (is_byte_load) begin
          w_data        = op_unsigned ? 8'h00 : {8{r[7]}}; // LBU : LB
          w_we          = 1'b1;
        end else
          w_we          = !is_store;
      end

      default: ;
    endcase

    case (state)
      E_MEM_LO, E_MEM_HI: begin
        dout = r2;  // rs2 via port 2 (low-fanout path to uio_out)
        rwb  = !is_store;
      end
      default: ;
    endcase

    // Flush: interrupts or instruction jump (JR, RETI, BRK).
    fetch_flush = take_nmi || take_irq || jump;
  end

  // ==========================================================================
  // FSM (negedge clk)
  // ==========================================================================

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state   <= E_IDLE;
      ir      <= 16'h0000;
      tmp     <= 16'h0000;
      pc      <= 16'h0000;
      i_bit   <= 1'b1;  // Interrupts disabled after reset
      nmi_ack <= 1'b0;
    end else begin
      // NMI handshake: set nmi_ack when NMI is taken; hold until project.v
      // clears nmi_pending, then release.
      if (!nmi_pending) nmi_ack <= 1'b0;
      else if (take_nmi) nmi_ack <= 1'b1;

      // ---------------------------------------------------------------------
      // State machine: transitions and per-state effects.
      // Ordered before interrupt entry and dispatch so their overrides
      // (state, i_bit, pc, ir) take priority via last-NBA-wins.
      // ---------------------------------------------------------------------
      case (state)
        E_IDLE: ;

        E_EXEC_LO: begin
          if (op == OP_SEI) i_bit <= 1'b1;
          if (op == OP_CLI) i_bit <= 1'b0;
          if (op == OP_RETI) tmp[7:0] <= r;   // Capture banked R6 low byte
          if (is_branch || is_jump_imm || is_mem_addr || is_jr_jalr)
            tmp[7:0] <= alu_result;
          if (is_branch) tmp[8] <= |r;  // nz_lo
          if (is_shift) tmp[7:0] <= r;
          // RETI and INT are two-cycle (system group, not is_two_cycle)
          if (op == OP_RETI || is_int || is_two_cycle)
            state <= E_EXEC_HI;
          else
            state <= E_IDLE;
        end

        E_EXEC_HI: begin
          if (is_mem_addr) begin
            // Address high byte computed; set up for memory access or complete.
            tmp[15:8] <= alu_result;
            state     <= (op == OP_AUIPC) ? E_IDLE : E_MEM_LO;
          end else if (is_jr_jalr) begin
            pc    <= next_pc;
            state <= E_IDLE;
          end else begin
            if (op == OP_RETI) i_bit <= tmp[0];
            if (jump) pc <= next_pc;
            state <= E_IDLE;
          end
        end

        E_MEM_LO: begin
          tmp[7:0] <= tmp[7:0] + 8'd1;  // Increment for E_MEM_HI address
          state    <= is_byte_store ? E_IDLE : E_MEM_HI;
        end

        E_MEM_HI: state <= E_IDLE;

        default: state <= 3'bx;
      endcase

      // ---------------------------------------------------------------------
      // Interrupt entry: synthesize an INT instruction in ir.
      // ir = {7'b1111100, vector_id[5:0], 3'd6} decodes as OP_INT with
      // off6 = vector_id and write dest = R6 (banked).
      // ---------------------------------------------------------------------
      if (take_nmi || take_irq) begin
        ir    <= {7'b1111100, 5'b00000, take_nmi, 3'd6};  // IRQ=0, NMI=1
        pc[0] <= i_bit;            // Stash old I flag in pc bit 0
        i_bit <= 1'b1;
        state <= E_EXEC_LO;
      end

      // ---------------------------------------------------------------------
      // Instruction dispatch: load ir from fetch and advance PC.
      // INT instructions (including BRK) set I=1 and stash old I in pc[0].
      // ---------------------------------------------------------------------
      if (ir_accept) begin
        pc <= pc + 16'd2;
        ir <= fetch_ir;
        if (fetch_ir[15:9] == 7'b1111100) begin
          pc[0] <= i_bit;         // Stash I flag
          i_bit <= 1'b1;
        end
        state <= E_EXEC_LO;
      end
    end
  end

endmodule
