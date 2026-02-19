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
// All decode properties are derived directly from ir bits — no intermediate
// opcode encoding. This makes decode cost transparent for ISA optimization.
//
// All instructions dispatch to E_EXEC_LO, then optionally continue to
// E_EXEC_HI (two-cycle ops). Memory instructions proceed from E_EXEC_HI
// to E_MEM_LO/HI for bus access.
//
// Register file ports are 16 bits wide. All regfile writes are deferred to
// E_EXEC_HI (or E_MEM_HI), so source operands are never corrupted
// mid-instruction. The ALU serializes 8 bits at a time internally; tmp[7:0]
// holds the lo-byte result between E_EXEC_LO and E_EXEC_HI.
//
// ISA encoding: variable-width prefix-free encoding
// -------------------------------------------------
// Prefix at MSB, registers at LSB for fixed positions.
//
//   Level  Format  Layout                             Instructions
//   5      R,8     [prefix:5|imm8:8|reg:3]            21: ADDI..SBS
//   6      R,7     [prefix:6|imm7:7|reg:3]            2: LUI,AUIPC
//   6      "10"    [prefix:6|imm10:10]                 2: J,JAL
//   7      R,R,R   [prefix:7|rd:3|rs2:3|rs1:3]       10: ADD..SRA
//   8      "8"     [prefix:8|off8:8]                   2: BT,BF
//   9      R,4     [prefix:9|shamt:4|reg:3]            3: SLLI,SRLI,SRAI
//  10      R,R     [prefix:10|rd:3|rs:3]               5: LWR..SBR
//  11-16   System  (full-width decode)                 11: SEI..STP
//
// ADDI has prefix 0000 so that 0x0000 = ADDI R0, 0 = NOP.
// T flag: single-bit condition flag set by comparisons (CMPI, CMPUI, XORIF,
// SLT, SLTU), tested by BT/BF branches. SR = {I, T}; ESR saves SR on INT.
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
    output wire        bus_active,
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
  // Cycle-to-cycle temporary (mem addr, branch target, ALU/shift result).
  // All bits use gated latches — zero DFFs.
  wire [7:0] tmp_lo;      // Latched at E_EXEC_LO (lo-byte result / addr lo)
  wire [7:0] tmp_hi;      // Latched at E_EXEC_HI (hi-byte addr)
  reg        carry_r;     // ALU carry (DFF — feeds ci_ext, can't be latch)
  reg        mem_carry;   // Low-byte all-ones carry for E_MEM_HI increment
  reg        saved_i_bit; // i_bit save for INT, captured at negedge when fsm_ready
  wire [15:0] tmp = {tmp_hi, tmp_lo};

  // Interrupt and PC state
  reg [15:1] pc;        // Program counter (word address; byte addr = {pc, 1'b0})
  reg        i_bit;     // Interrupt disable flag (0=enabled, 1=disabled)
  reg        t_bit;     // T flag (condition result from comparisons)
  reg  [1:0] esr;       // Exception status register: saved {i_bit, t_bit}

  // -------------------------------------------------------------------------
  // Instruction decode: all properties derived directly from ir
  // -------------------------------------------------------------------------

  // --- R,8 format (5-bit prefix @ [15:11]) ---
  wire is_addi = ir[15:11] == 5'b00000;
  wire is_li   = ir[15:11] == 5'b00001;
  wire is_lw   = ir[15:11] == 5'b00010;
  wire is_lb   = ir[15:11] == 5'b00011;
  wire is_lbu  = ir[15:11] == 5'b00100;
  wire is_sw   = ir[15:11] == 5'b00101;
  wire is_sb   = ir[15:11] == 5'b00110;
  wire is_jr   = ir[15:11] == 5'b00111;
  wire is_jalr = ir[15:11] == 5'b01000;


  wire is_andi  = ir[15:11] == 5'b01001;
  wire is_ori   = ir[15:11] == 5'b01010;
  wire is_xori  = ir[15:11] == 5'b01011;
  wire is_slti  = ir[15:11] == 5'b01100;
  wire is_sltui = ir[15:11] == 5'b01101;
  wire is_bz    = ir[15:11] == 5'b01110;
  wire is_bnz   = ir[15:11] == 5'b01111;
  wire is_xorif = ir[15:11] == 5'b10000;
  wire is_lw_s  = ir[15:11] == 5'b10001;
  wire is_lb_s  = ir[15:11] == 5'b10010;
  wire is_lbu_s = ir[15:11] == 5'b10011;
  wire is_sw_s  = ir[15:11] == 5'b10100;
  wire is_sb_s  = ir[15:11] == 5'b10101;
  // --- "8" format (8-bit prefix @ [15:8], offset @ [7:0]) ---
  wire is_bt = ir[15:8] == 8'b10110_000;
  wire is_bf = ir[15:8] == 8'b10110_001;

  // --- R,7 / "10" format (6-bit prefix @ [15:10]) ---
  wire is_lui   = ir[15:10] == 6'b110100;
  wire is_auipc = ir[15:10] == 6'b110101;
  wire is_j     = ir[15:10] == 6'b110110;
  wire is_jal   = ir[15:10] == 6'b110111;

  // --- R,R,R format (7-bit prefix @ [15:9]) ---
  wire is_add  = ir[15:9] == 7'b1110000;
  wire is_sub  = ir[15:9] == 7'b1110001;
  wire is_and  = ir[15:9] == 7'b1110010;
  wire is_or   = ir[15:9] == 7'b1110011;
  wire is_xor  = ir[15:9] == 7'b1110100;
  wire is_slt  = ir[15:9] == 7'b1110101;
  wire is_sltu = ir[15:9] == 7'b1110110;
  wire is_sll  = ir[15:9] == 7'b1110111;
  wire is_srl  = ir[15:9] == 7'b1111000;
  wire is_sra  = ir[15:9] == 7'b1111001;

  // --- R,4 format (9-bit prefix @ [15:7]) ---
  wire is_slli = ir[15:7] == 9'b111101000;
  wire is_srli = ir[15:7] == 9'b111101001;
  wire is_srai = ir[15:7] == 9'b111101010;

  // --- R,R format (10-bit prefix @ [15:6]) ---
  wire is_lw_rr  = ir[15:6] == 10'b1111010110;
  wire is_lb_rr  = ir[15:6] == 10'b1111010111;
  wire is_lbu_rr = ir[15:6] == 10'b1111011000;
  wire is_sw_rr  = ir[15:6] == 10'b1111011001;
  wire is_sb_rr  = ir[15:6] == 10'b1111011010;

  // --- System format (full-width decode) ---
  wire is_sei  = ir == 16'b1111100000000001;
  wire is_cli  = ir == 16'b1111100000000010;
  wire is_reti = ir == 16'b1111100000000011;
  wire is_wai  = ir == 16'b1111100000000101;
  wire is_stp  = ir == 16'b1111100000000111;
  wire is_epcr = ir[15:3] == 13'b1111100000010;
  wire is_epcw = ir[15:3] == 13'b1111100000011;
  wire is_movt = ir[15:3] == 13'b1111100000_100;
  wire is_srr  = ir[15:3] == 13'b1111100000_101;
  wire is_srw  = ir[15:3] == 13'b1111100000_001;
  wire is_int  = ir[15:4] == 12'b1111100000_11;

  // --- Behavioral groups ---

  localparam LINK_REG = 3'd6;

  // Memory groups
  wire is_r9_load  = is_lw || is_lb || is_lbu;
  wire is_r9_store = is_sw || is_sb;
  wire is_rr_load  = is_lw_rr || is_lb_rr || is_lbu_rr;
  wire is_rr_store = is_sw_rr || is_sb_rr;
  wire is_sp_load  = is_lw_s || is_lb_s || is_lbu_s;
  wire is_sp_store = is_sw_s || is_sb_s;
  // Combined memory properties for E_MEM and r_hi
  wire mem_is_store      = is_r9_store || is_rr_store || is_sp_store;
  wire mem_is_byte_load  = is_lb || is_lbu || is_lb_rr || is_lbu_rr || is_lb_s || is_lbu_s;
  wire mem_is_byte_store = is_sb || is_sb_rr || is_sb_s;
  wire mem_is_lbu        = is_lbu || is_lbu_rr || is_lbu_s;

  // R,R,R group
  wire is_rrr = is_add || is_sub || is_and || is_or || is_xor
              || is_slt || is_sltu || is_sll || is_srl || is_sra;
  wire is_alu_rrr  = is_add || is_sub || is_and || is_or || is_xor;
  wire is_slt_rrr  = is_slt || is_sltu;
  wire is_shift_rr = is_sll || is_srl || is_sra;

  // Shift groups
  wire is_shift_imm   = is_slli || is_srli || is_srai;
  wire is_shift       = is_shift_rr || is_shift_imm;
  wire is_right_shift = is_srl || is_sra || is_srli || is_srai;
  wire is_arith_shift = is_sra || is_srai;

  // T-flag comparisons (set T, no register write)
  wire is_cmp_imm = is_slti || is_sltui || is_xorif;

  // Jump/branch
  wire is_branch   = is_bz || is_bnz;
  wire is_t_branch = is_bt || is_bf;
  wire is_jump_imm = is_j || is_jal;
  wire is_jr_jalr  = is_jr || is_jalr;

  // WAI/STP go directly from E_EXEC_LO to E_IDLE (2-cycle, no E_EXEC_HI visit)

  // ==========================================================================
  // Shared Infrastructure
  // ==========================================================================

  // -------------------------------------------------------------------------
  // Register file (16-bit interface)
  // -------------------------------------------------------------------------
  reg  [3:0]  r1_sel;
  wire [15:0] r1;
  wire [15:0] r2;
  reg         w_we;
  reg  [15:0] w_data;

  // Pipeline registers (transparent-high latches): capture write signals at
  // negedge for the register file.  These bridge the compute phase (clk=1)
  // to the write phase (clk=0), providing stable inputs during writes.
  // Every pipelined RISC CPU has equivalent staging; this is pipeline
  // infrastructure, not register-file overhead.
  wire [15:0] w_data_r;
  wire [3:0]  w_sel_r;
  wire        w_we_r;

  generate
    genvar pi;
    for (pi = 0; pi < 16; pi = pi + 1) begin : gen_wr_data
      sg13g2_dlhrq_1 u_wr_data (
        .D(w_data[pi]), .GATE(clk), .RESET_B(rst_n), .Q(w_data_r[pi])
      );
    end
    for (pi = 0; pi < 4; pi = pi + 1) begin : gen_wr_sel
      sg13g2_dlhrq_1 u_wr_sel (
        .D(w_sel_mux[pi]), .GATE(clk), .RESET_B(rst_n), .Q(w_sel_r[pi])
      );
    end
  endgenerate

  sg13g2_dlhrq_1 u_wr_we (
    .D(w_we), .GATE(clk), .RESET_B(rst_n), .Q(w_we_r)
  );

  // -------------------------------------------------------------------------
  // Temporary register: gated latches + 1 DFF (carry_r).
  //   tmp_lo[7:0]:  ICG at E_EXEC_LO
  //   tmp_hi[7:0]:  ICG at E_EXEC_HI
  //   carry_r:      DFF (feeds ALU ci_ext — latch would create STA false path)
  //   saved_i_bit:  DFF at negedge (full-period constraint avoids half-period
  //                 ICG path through ALU carry chain → insn_completing → fsm_ready)
  // -------------------------------------------------------------------------
  wire gclk_tmp_lo, gclk_tmp_hi;
  sg13g2_lgcp_1 u_icg_tmp_lo (.CLK(clk), .GATE(state == E_EXEC_LO), .GCLK(gclk_tmp_lo));
  sg13g2_lgcp_1 u_icg_tmp_hi (.CLK(clk), .GATE(state == E_EXEC_HI), .GCLK(gclk_tmp_hi));

  generate
    genvar ti;
    for (ti = 0; ti < 8; ti = ti + 1) begin : gen_tmp_lo
      sg13g2_dlhrq_1 u_tmp (
        .D(next_tmp_lo[ti]), .GATE(gclk_tmp_lo), .RESET_B(rst_n), .Q(tmp_lo[ti])
      );
    end
    for (ti = 0; ti < 8; ti = ti + 1) begin : gen_tmp_hi
      sg13g2_dlhrq_1 u_tmp (
        .D(alu_result[ti]), .GATE(gclk_tmp_hi), .RESET_B(rst_n), .Q(tmp_hi[ti])
      );
    end
  endgenerate

  wire is_mem_phase = (state == E_MEM_LO || state == E_MEM_HI);

  // -------------------------------------------------------------------------
  // Bus outputs (state-independent: only depends on memory phase)
  // -------------------------------------------------------------------------
  assign bus_active = is_mem_phase;

  always @(*) begin
    ab = 16'bx;
    if (state == E_MEM_LO)
      ab = tmp;
    else if (state == E_MEM_HI)
      ab = {tmp[15:8] + {7'd0, mem_carry}, tmp[7:0] + 8'd1};
  end

  always @(*) begin
    dout = 8'bx;
    rwb  = 1'bx;
    if (is_mem_phase) begin
      dout = r2_hi_r ? r2[15:8] : r2[7:0];
      rwb  = !mem_is_store;
    end
  end

  // w_sel: write port register select (4-bit: bit 3 selects EPC)
  reg [3:0] w_sel_mux;
  always @(*) begin
    if (is_int || is_epcw)
      w_sel_mux = 4'd8;                                        // INT/EPCW → EPC
    else if (is_jal)
      w_sel_mux = {1'b0, LINK_REG};                            // JAL → R6
    else if (is_rrr)
      w_sel_mux = {1'b0, ir[8:6]};                             // R,R,R: rd at [8:6]
    else if (is_mem_phase && is_rr_load)
      w_sel_mux = {1'b0, ir[5:3]};                             // R,R loads: rd at [5:3]
    else
      w_sel_mux = {1'b0, ir[2:0]};                             // Default: reg at [2:0]
  end

  // r2_sel: read port 2 register select
  //   Default ir[5:3] works for R,R,R (rs2) and R,R loads/stores (rd/data).
  //   Override to ir[2:0] for R,9 and SP stores (data reg in R,8 reg field).
  reg [2:0] r2_sel;
  always @(*) begin
    if (is_r9_store || is_sp_store) r2_sel = ir[2:0];
    else                            r2_sel = ir[5:3];
  end
  reg        r2_hi_r;   // dout byte select: 0=r2[7:0], 1=r2[15:8]

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .w_sel  (w_sel_r),
    .w_data (w_data_r),
    .w_we   (w_we_r),
    .r1_sel (r1_sel),
    .r1     (r1),
    .r2_sel (r2_sel),
    .r2     (r2)
  );

  // -------------------------------------------------------------------------
  // ALU
  // -------------------------------------------------------------------------
  reg  [7:0] alu_a;
  reg  [7:0] alu_b;
  reg  [2:0] alu_op;
  wire [7:0] alu_result;
  wire       alu_co;

  // alu_new_op: always 1 in E_EXEC_LO (new operation), 0 in E_EXEC_HI (carry continuation)
  wire alu_new_op = (state == E_EXEC_LO);

  riscyv02_alu u_alu (
    .a      (alu_a),
    .b      (alu_b),
    .op     (alu_op),
    .new_op (alu_new_op),
    .ci_ext (carry_r),
    .co     (alu_co),
    .result (alu_result)
  );

  // -------------------------------------------------------------------------
  // Barrel shifter
  // -------------------------------------------------------------------------
  wire [3:0] shamt = is_shift_rr ? r2[3:0] : ir[6:3];

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

  // -------------------------------------------------------------------------
  // Combinational register-file select from (state, ir)
  // -------------------------------------------------------------------------

  // r1_sel: read port 1 register select
  //   Default ir[2:0] works for all formats: reg/rs1/rs is always at [2:0].
  //   R,9 loads/stores override to R0 during execute (base address).
  //   SP loads/stores override to R7 during execute (base address).
  //   During E_MEM readback, R,9 and SP loads read ir[2:0] (data register).
  always @(*) begin
    if (is_mem_phase && !mem_is_store) begin
      if (is_r9_load || is_sp_load) r1_sel = {1'b0, ir[2:0]};  // data reg readback
      else                          r1_sel = {1'b0, ir[5:3]};   // R,R load dest
    end else if (is_reti || is_epcr)
      r1_sel = 4'd8;                                   // EPC (entry 8)
    else if (is_r9_load || is_r9_store)
      r1_sel = 4'd0;                                   // R0 base
    else if (is_sp_load || is_sp_store)
      r1_sel = 4'd7;                                   // SP (R7) base
    else
      r1_sel = {1'b0, ir[2:0]};                        // Default: reg at [2:0]
  end

  // -------------------------------------------------------------------------
  // Combinational intermediates and next-state values
  // -------------------------------------------------------------------------
  reg        insn_completing;
  reg        jump;

  // Next-state values for all DFFs (computed in combinational block)
  reg [2:0]  next_state;
  reg [15:0] next_ir;
  reg        next_carry_r;
  reg [15:1] next_pc;
  reg        next_i_bit;
  reg        next_t_bit;
  reg [1:0]  next_esr;
  reg        next_r2_hi_r;
  reg        next_mem_carry;

  // Combinational signal for tmp[7:0] latch at E_EXEC_LO negedge
  reg [7:0]  next_tmp_lo;

  // Interrupt control
  wire fsm_ready = (state == E_IDLE) || insn_completing;
  wire take_nmi = fsm_ready && (nmi_pending || nmi_edge) && !nmi_ack;
  // Forwarded i_bit: reflects SEI/CLI/SRW/RETI effect completing this cycle
  reg i_bit_fwd;
  always @(*) begin
    if (is_srw)       i_bit_fwd = r1[1];
    else if (is_reti) i_bit_fwd = esr[1];
    else              i_bit_fwd = (i_bit || is_sei) && !is_cli;
  end
  wire take_irq = fsm_ready && !irqb && !i_bit_fwd && !take_nmi;
  assign ir_accept = fsm_ready && ir_valid && !fetch_flush;
  assign waiting = (state == E_IDLE) && is_wai;
  assign stopped = (state == E_IDLE) && is_stp;

  // ==========================================================================
  // State-Property Block
  // ==========================================================================

  assign fetch_pc = {pc, 1'b0};

  always @(*) begin
    // --- Next-state defaults: hold all registers ---
    next_state      = state;
    next_ir         = ir;
    next_carry_r    = carry_r;
    next_pc         = pc;
    next_i_bit      = i_bit;
    next_t_bit      = t_bit;
    next_esr        = esr;
    next_r2_hi_r    = r2_hi_r;
    next_mem_carry  = mem_carry;

    // --- Output defaults ---
    alu_a           = r1[7:0];
    alu_b           = 8'bx;
    alu_op          = 3'd0;    // ADD
    w_data          = {alu_result, tmp[7:0]};
    w_we            = 1'b0;
    insn_completing = 1'b0;
    jump            = 1'b0;
    shifter_din     = 15'b0;
    next_tmp_lo     = alu_result;

    case (state)
      E_EXEC_LO: begin
        if (is_reti) begin
          // EPC available on r1[15:0]; no action needed in LO
        end else if (is_int) begin
          // Deferred to E_EXEC_HI
        end else if (is_epcr || is_epcw || is_movt || is_srr || is_srw) begin
          // Deferred to E_EXEC_HI
        end else if (is_r9_load || is_r9_store || is_sp_load || is_sp_store) begin
          // Address: base + sext(imm8), byte offset (no shift)
          alu_b      = ir[10:3];            // imm[7:0]
        end else if (is_auipc) begin
          // pc + (sext(imm7) << 9): lo byte is pc + 0
          alu_a       = {pc[7:1], 1'b0};
          alu_b       = 8'h00;
          next_tmp_lo = alu_result;
        end else if (is_rr_load || is_rr_store) begin
          // Address = rs, no offset
          alu_b      = 8'd0;
        end else if (is_jr_jalr) begin
          // JR/JALR: rs + sext(imm8) (byte offset, no shift)
          alu_b      = ir[10:3];            // imm[7:0]
          // JR same-page: high byte unchanged, 1 exec cycle
          if (is_jr && (alu_co == ir[10])) begin
            jump            = 1'b1;
            next_pc         = {r1[15:8], alu_result[7:1]};
            insn_completing = 1'b1;
          end
        end else if (is_addi) begin
          alu_b       = ir[10:3];            // imm[7:0]
          next_tmp_lo = alu_result;
        end else if (is_li) begin
          next_tmp_lo = ir[10:3];            // imm[7:0]
        end else if (is_alu_rrr) begin
          alu_op      = ir[11:9];            // ADD=0, SUB=1, AND=2, OR=3, XOR=4
          alu_b       = r2[7:0];
          next_tmp_lo = alu_result;
        end else if (is_slt_rrr) begin
          alu_op     = 3'd1;                // SUB for comparison
          alu_b      = r2[7:0];
          // No write — just save borrow for E_EXEC_HI
        end else if (is_andi) begin
          alu_op      = 3'd2;
          alu_b       = ir[10:3];            // imm8 (zero-extended: hi byte = 0 in HI)
          next_tmp_lo = alu_result;
        end else if (is_ori) begin
          alu_op      = 3'd3;
          alu_b       = ir[10:3];
          next_tmp_lo = alu_result;
        end else if (is_xori) begin
          alu_op      = 3'd4;
          alu_b       = ir[10:3];
          next_tmp_lo = alu_result;
        end else if (is_xorif) begin
          alu_op      = 3'd4;
          alu_b       = ir[10:3];
          next_tmp_lo = alu_result;
        end else if (is_slti || is_sltui) begin
          alu_op     = 3'd1;                // SUB
          alu_b      = ir[10:3];            // imm8 low byte
          // No write — just save borrow for E_EXEC_HI
        end else if (is_shift) begin
          if (shamt[3]) begin
            // Cross-byte: fill byte for the vacated half
            if (is_right_shift)
              next_tmp_lo = is_arith_shift ? {8{r1[15]}} : 8'h00;
            else
              next_tmp_lo = 8'h00;
          end else if (is_right_shift) begin
            // Right shift hi byte: fill from sign/zero, input is {fill, r1[15:8]}
            shifter_din = {is_arith_shift ? {7{r1[15]}} : 7'b0, r1[15:8]};
            next_tmp_lo = shifter_result;
          end else begin
            // Left shift lo byte: reverse, right-shift, reverse
            shifter_din = {7'b0, rev8(r1[7:0])};
            next_tmp_lo = rev8(shifter_result);
          end
        end else if (is_lui) begin
          next_tmp_lo = 8'h00;           // lo byte = 0 (sext(imm7) << 9)
        end else if (is_branch) begin
          alu_a      = {pc[7:1], 1'b0};
          alu_b      = {ir[3], ir[9:4], 1'b0};  // RISC-V trick: off[6],off[5:0],0
          // Same-page taken: high byte unchanged, 1 exec cycle (3 total)
          if ((!(|r1) ^ is_bnz) && (alu_co == ir[10])) begin
            jump            = 1'b1;
            next_pc         = {pc[15:8], alu_result[7:1]};
            insn_completing = 1'b1;
          end
        end else if (is_t_branch) begin
          alu_a      = {pc[7:1], 1'b0};
          alu_b      = {ir[6:0], 1'b0};     // off8[6:0] << 1
          // Same-page taken: high byte unchanged, 1 exec cycle (3 total)
          if ((t_bit ^ is_bf) && (alu_co == ir[7])) begin
            jump            = 1'b1;
            next_pc         = {pc[15:8], alu_result[7:1]};
            insn_completing = 1'b1;
          end
        end else if (is_jump_imm) begin
          alu_a      = {pc[7:1], 1'b0};
          alu_b      = {ir[6:0], 1'b0};     // off10[6:0] << 1
          // J same-page (small offset): high byte unchanged, 1 exec cycle
          if (is_j && ir[8:7] == {2{ir[9]}} && (alu_co == ir[9])) begin
            jump            = 1'b1;
            next_pc         = {pc[15:8], alu_result[7:1]};
            insn_completing = 1'b1;
          end
        end

        // State transition
        next_carry_r = alu_co;
        if (is_wai || is_stp)
          next_state = E_IDLE;
        else if (insn_completing)
          next_state = E_IDLE;
        else
          next_state = E_EXEC_HI;
      end

      E_EXEC_HI: begin
        if (is_r9_load || is_r9_store || is_sp_load || is_sp_store) begin
          // Address high byte: sign-extend imm bit 7
          alu_a      = r1[15:8];
          alu_b      = {8{ir[10]}};
          next_state = E_MEM_LO;
        end else if (is_auipc) begin
          alu_a           = pc[15:8];
          alu_b           = {ir[9:3], 1'b0};    // (sext(imm7) << 9) hi byte
          w_we            = 1'b1;
          insn_completing = 1'b1;
          next_state      = E_IDLE;
        end else if (is_rr_load || is_rr_store) begin
          // Address high byte: carry propagation only
          alu_a      = r1[15:8];
          alu_b      = 8'd0;
          next_state = E_MEM_LO;
        end else if (is_jr_jalr) begin
          // JR/JALR high byte: sign-extend imm bit 7
          alu_a           = r1[15:8];
          alu_b           = {8{ir[10]}};
          jump            = 1'b1;
          next_pc         = {alu_result, tmp[7:1]};
          insn_completing = 1'b1;
          if (is_jalr) begin
            w_data = {pc, 1'b0};
            w_we   = 1'b1;
          end
          next_state = E_IDLE;
        end else begin
          if (is_reti) begin
            jump       = 1'b1;
            next_pc    = r1[15:1];               // EPC is clean 16-bit address
            next_i_bit = esr[1];
            next_t_bit = esr[0];
          end else if (is_int) begin
            w_data  = {pc, 1'b0};             // EPC = clean return address
            w_we    = 1'b1;
            jump    = 1'b1;
            next_pc = {13'b0, ir[1:0] + 2'd1};
            next_esr = {saved_i_bit, t_bit};
          end else begin
          // Execute high byte: completes this cycle
          insn_completing = 1'b1;
          if (is_addi) begin
            alu_a      = r1[15:8];
            alu_b      = {8{ir[10]}};           // sign-extend imm bit 7
            w_we       = 1'b1;
          end else if (is_li) begin
            w_data = {{8{ir[10]}}, tmp[7:0]};  // sign-extend imm bit 7
            w_we   = 1'b1;
          end else if (is_alu_rrr) begin
            alu_a      = r1[15:8];
            alu_op     = ir[11:9];
            alu_b      = r2[15:8];
            w_we       = 1'b1;
          end else if (is_slt_rrr) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd1;
            alu_b      = r2[15:8];
            if (is_sltu)
              next_t_bit = ~alu_co;
            else
              next_t_bit = (r1[15] ^ r2[15]) ? r1[15] : alu_result[7];
          end else if (is_andi) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd2;
            alu_b      = 8'h00;                 // zero-extend
            w_we       = 1'b1;
          end else if (is_ori) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd3;
            alu_b      = 8'h00;
            w_we       = 1'b1;
          end else if (is_xori) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd4;
            alu_b      = 8'h00;
            w_we       = 1'b1;
          end else if (is_xorif) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd4;
            alu_b      = 8'h00;
            // T = (result != 0): any bit set in lo or hi
            next_t_bit = (|tmp[7:0]) || (|alu_result);
          end else if (is_slti) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd1;
            alu_b      = {8{ir[10]}};           // sign-extend imm8 bit 7
            next_t_bit = (r1[15] ^ ir[10]) ? r1[15] : alu_result[7];
          end else if (is_sltui) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd1;
            alu_b      = {8{ir[10]}};           // sign-extend for unsigned comparison
            next_t_bit = ~alu_co;
          end else if (is_shift) begin
            if (shamt[3]) begin
              if (is_right_shift) begin
                shifter_din = {is_arith_shift ? {7{r1[15]}} : 7'b0, r1[15:8]};
                w_data = {tmp[7:0], shifter_result};
                w_we   = 1'b1;
              end else begin
                shifter_din = {7'b0, rev8(r1[7:0])};
                w_data = {rev8(shifter_result), tmp[7:0]};
                w_we   = 1'b1;
              end
            end else if (is_right_shift) begin
              shifter_din = {r1[14:8], r1[7:0]};
              w_data = {tmp[7:0], shifter_result};
              w_we   = 1'b1;
            end else begin
              shifter_din = {rev7(r1[7:1]), rev8(r1[15:8])};
              w_data = {rev8(shifter_result), tmp[7:0]};
              w_we   = 1'b1;
            end
          end else if (is_lui) begin
            w_data = {{ir[9:3], 1'b0}, tmp[7:0]};  // (sext(imm7) << 9) hi byte
            w_we   = 1'b1;
          end else if (is_branch) begin
            alu_a      = pc[15:8];
            alu_b      = {8{ir[10]}};           // sign-extend off8 bit 7
            // BZ/BNZ: full 16-bit zero check (r1[15:0] stable, no write in LO)
            if (!(|r1) ^ is_bnz) begin
              jump    = 1'b1;
              next_pc = {alu_result, tmp[7:1]};
            end
          end else if (is_t_branch) begin
            alu_a      = pc[15:8];
            alu_b      = {8{ir[7]}};            // sign-extend off8 bit 7
            if (t_bit ^ is_bf) begin
              jump    = 1'b1;
              next_pc = {alu_result, tmp[7:1]};
            end
          end else if (is_jump_imm) begin
            alu_a      = pc[15:8];
            alu_b      = {{6{ir[9]}}, ir[8], ir[7]};  // sext(off10[9:7])
            jump       = 1'b1;
            next_pc    = {alu_result, tmp[7:1]};
            if (is_jal) begin
              w_data = {pc, 1'b0};
              w_we   = 1'b1;
            end
          end else if (is_epcr || is_epcw) begin
            w_data = r1;
            w_we   = 1'b1;
          end else if (is_movt) begin
            w_data = {15'b0, t_bit};
            w_we   = 1'b1;
          end else if (is_srr) begin
            w_data = {14'b0, i_bit, t_bit};
            w_we   = 1'b1;
          end
          // Flag effects (mutually exclusive with each other)
          if (is_sei) next_i_bit = 1'b1;
          if (is_cli) next_i_bit = 1'b0;
          if (is_srw) begin
            next_i_bit = r1[1];
            next_t_bit = r1[0];
          end
          end
          next_state = E_IDLE;
        end
      end

      E_MEM_LO: begin
        if (mem_is_byte_store)
          insn_completing = 1'b1;
        else if (mem_is_byte_load) begin
          // Byte loads complete here: sign/zero-extend and write directly
          insn_completing = 1'b1;
          if (mem_is_lbu)
            w_data = {8'h00, uio_in};
          else
            w_data = {{8{uio_in[7]}}, uio_in};
          w_we = 1'b1;
        end else if (!mem_is_store) begin
          // Word load: write lo byte, preserve hi byte (read back at E_MEM_HI)
          w_data = {r1[15:8], uio_in};
          w_we   = 1'b1;
        end
        next_r2_hi_r   = mem_is_store;
        next_mem_carry = &tmp[7:0];
        next_state     = (mem_is_byte_store || mem_is_byte_load) ? E_IDLE : E_MEM_HI;
      end

      E_MEM_HI: begin
        insn_completing = 1'b1;
        w_data          = {uio_in, r1[7:0]};
        w_we            = !mem_is_store;
        next_state      = E_IDLE;
      end

      E_IDLE: ;
      default: next_state = 3'bx;
    endcase

    // -----------------------------------------------------------------
    // Interrupt entry (overrides state machine)
    // -----------------------------------------------------------------
    if (take_nmi || take_irq) begin
      next_ir    = {10'b1111100000, 2'b11, 2'b00, !take_nmi, 1'b0};
      next_i_bit = 1'b1;
      next_state = E_EXEC_LO;
    end

    // -----------------------------------------------------------------
    // Instruction dispatch (overrides everything)
    // -----------------------------------------------------------------
    if (ir_accept) begin
      next_pc    = pc + 15'd1;
      next_ir    = fetch_ir;
      next_r2_hi_r = 1'b0;
      if (fetch_ir[15:4] == 12'b1111100000_11)
        next_i_bit = 1'b1;
      next_state = E_EXEC_LO;
    end

    fetch_flush = take_nmi || take_irq || jump;
  end

  // ==========================================================================
  // Sequential (negedge clk): register next-state values
  // ==========================================================================

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state     <= E_IDLE;
      ir        <= 16'h0000;
      carry_r   <= 1'b0;
      pc        <= 15'h0000;
      i_bit     <= 1'b1;
      t_bit     <= 1'b0;
      esr       <= 2'b10;  // {I=1, T=0}
      r2_hi_r   <= 1'b0;
      mem_carry <= 1'b0;
    end else begin
      state     <= next_state;
      ir        <= next_ir;
      carry_r   <= next_carry_r;
      pc        <= next_pc;
      i_bit     <= next_i_bit;
      t_bit     <= next_t_bit;
      esr       <= next_esr;
      r2_hi_r   <= next_r2_hi_r;
      mem_carry <= next_mem_carry;
    end
  end

  // NMI handshake: set has priority (take_nmi fires via nmi_edge
  // before nmi_pending is registered, so nmi_pending may still be 0).
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n)
      nmi_ack <= 1'b0;
    else if (take_nmi)
      nmi_ack <= 1'b1;
    else if (!nmi_pending)
      nmi_ack <= 1'b0;
  end

  // Capture i_bit_fwd at dispatch (negedge DFF: full-period constraint).
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n)
      saved_i_bit <= 1'b1;
    else if (fsm_ready)
      saved_i_bit <= i_bit_fwd;
  end

endmodule
