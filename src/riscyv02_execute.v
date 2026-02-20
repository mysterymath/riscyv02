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
// ISA encoding: RV32I-style 16-bit encoding
// ------------------------------------------
// Fixed 5-bit opcode at [4:0]. Register rs1/rd at [7:5]. Sign at [15].
// Immediates at [15:8] with sign always at ir[15].
//
//   Format  Layout (MSB to LSB)                              Instructions
//   I       [imm8:8|rs/rd:3|opcode:5]                        24 (incl LUI,AUIPC)
//   B       [imm8:8|funct3:3|opcode:5]                       BT, BF
//   J       [s:1|imm[6:0]:7|imm[8:7]:2|fn1:1|opcode:5]      J, JAL
//   R       [fn2:2|rd:3|rs2:3|rs1:3|opcode:5]                R,R,R(8) + R,R(8)
//   SI      [fn2:2|dc:2|shamt:4|rs/rd:3|opcode:5]            SLLI,SRLI,SRAI
//   SYS     [sub:8|reg:3|opcode:5]                           11 system insns
//
// ADDI has opcode 0 so that 0x0000 = ADDI R0, 0 = NOP.
// T flag: single-bit condition flag set by comparisons (CLTI, CLTUI, CEQI,
// CLT, CLTU, CEQ), tested by BT/BF branches. SR = {I, T}; ESR saves SR on INT.
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
  // Declared as regs below, captured in the main sequential block.
  reg        carry_r;     // ALU carry (DFF — feeds ci_ext)
  // mem_carry eliminated: tmp is pre-incremented at E_MEM_LO (registered)
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

  // --- I-type (opcode at [4:0]) ---
  wire is_addi  = ir[4:0] == 5'd0;
  wire is_li    = ir[4:0] == 5'd1;
  wire is_lw    = ir[4:0] == 5'd2;
  wire is_lb    = ir[4:0] == 5'd3;
  wire is_lbu   = ir[4:0] == 5'd4;
  wire is_sw    = ir[4:0] == 5'd5;
  wire is_sb    = ir[4:0] == 5'd6;
  wire is_jr    = ir[4:0] == 5'd7;
  wire is_jalr  = ir[4:0] == 5'd8;
  wire is_andi  = ir[4:0] == 5'd9;
  wire is_ori   = ir[4:0] == 5'd10;
  wire is_xori  = ir[4:0] == 5'd11;
  wire is_clti  = ir[4:0] == 5'd12;
  wire is_cltui = ir[4:0] == 5'd13;
  wire is_bz    = ir[4:0] == 5'd14;
  wire is_bnz   = ir[4:0] == 5'd15;
  wire is_ceqi  = ir[4:0] == 5'd16;
  wire is_lw_s  = ir[4:0] == 5'd17;
  wire is_lb_s  = ir[4:0] == 5'd18;
  wire is_lbu_s = ir[4:0] == 5'd19;
  wire is_sw_s  = ir[4:0] == 5'd20;
  wire is_sb_s  = ir[4:0] == 5'd21;
  wire is_lui   = ir[4:0] == 5'd22;
  wire is_auipc = ir[4:0] == 5'd23;

  // --- B-type (opcode 24, funct3 at [7:5]) ---
  wire is_bt = ir[4:0] == 5'd24 && ir[7:5] == 3'd0;
  wire is_bf = ir[4:0] == 5'd24 && ir[7:5] == 3'd1;

  // --- J-type (opcode 25, fn1 at [5]) ---
  wire is_j   = ir[4:0] == 5'd25 && !ir[5];
  wire is_jal = ir[4:0] == 5'd25 &&  ir[5];

  // --- R-type (opcodes 26-29, funct2 at [15:14]) ---
  // R-ALU1 (opcode 26): ADD=00, SUB=01, AND=10, OR=11
  wire is_add  = ir[4:0] == 5'd26 && ir[15:14] == 2'd0;
  wire is_sub  = ir[4:0] == 5'd26 && ir[15:14] == 2'd1;
  wire is_and  = ir[4:0] == 5'd26 && ir[15:14] == 2'd2;
  wire is_or   = ir[4:0] == 5'd26 && ir[15:14] == 2'd3;
  // R-ALU2 (opcode 27): XOR=00, SLL=01, SRL=10, SRA=11
  wire is_xor  = ir[4:0] == 5'd27 && ir[15:14] == 2'd0;
  wire is_sll  = ir[4:0] == 5'd27 && ir[15:14] == 2'd1;
  wire is_srl  = ir[4:0] == 5'd27 && ir[15:14] == 2'd2;
  wire is_sra  = ir[4:0] == 5'd27 && ir[15:14] == 2'd3;
  // R-MEM (opcode 28): LWR=00, LBR=01, LBUR=10, SWR=11
  wire is_lw_rr  = ir[4:0] == 5'd28 && ir[15:14] == 2'd0;
  wire is_lb_rr  = ir[4:0] == 5'd28 && ir[15:14] == 2'd1;
  wire is_lbu_rr = ir[4:0] == 5'd28 && ir[15:14] == 2'd2;
  wire is_sw_rr  = ir[4:0] == 5'd28 && ir[15:14] == 2'd3;
  // R-MISC (opcode 29): SBR=00, CLT=01, CLTU=10, CEQ=11
  wire is_sb_rr  = ir[4:0] == 5'd29 && ir[15:14] == 2'd0;
  wire is_clt    = ir[4:0] == 5'd29 && ir[15:14] == 2'd1;
  wire is_cltu   = ir[4:0] == 5'd29 && ir[15:14] == 2'd2;
  wire is_ceq    = ir[4:0] == 5'd29 && ir[15:14] == 2'd3;

  // --- SI-type (opcode 30, funct2 at [15:14]) ---
  wire is_slli = ir[4:0] == 5'd30 && ir[15:14] == 2'd0;
  wire is_srli = ir[4:0] == 5'd30 && ir[15:14] == 2'd1;
  wire is_srai = ir[4:0] == 5'd30 && ir[15:14] == 2'd2;

  // --- System (opcode 31, sub8 at [15:8]) ---
  wire is_sei  = ir[4:0] == 5'd31 && ir[15:8] == 8'h01;
  wire is_cli  = ir[4:0] == 5'd31 && ir[15:8] == 8'h02;
  wire is_reti = ir[4:0] == 5'd31 && ir[15:8] == 8'h03;
  wire is_wai  = ir[4:0] == 5'd31 && ir[15:8] == 8'h05;
  wire is_stp  = ir[4:0] == 5'd31 && ir[15:8] == 8'h07;
  wire is_srw  = ir[4:0] == 5'd31 && ir[15:8] == 8'h08;
  wire is_epcr = ir[4:0] == 5'd31 && ir[15:8] == 8'h10;
  wire is_epcw = ir[4:0] == 5'd31 && ir[15:8] == 8'h18;
  wire is_srr  = ir[4:0] == 5'd31 && ir[15:8] == 8'h28;
  wire is_int  = ir[4:0] == 5'd31 && ir[15:14] == 2'b11;

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

  // ALU R,R,R groups
  wire is_alu1     = ir[4:0] == 5'd26;   // ADD/SUB/AND/OR
  wire is_alu_rrr  = is_add || is_sub || is_and || is_or || is_xor;
  wire is_rrr      = is_alu_rrr || is_sll || is_srl || is_sra;
  wire is_shift_rr = is_sll || is_srl || is_sra;

  // Shift groups
  wire is_shift_imm   = is_slli || is_srli || is_srai;
  wire is_shift       = is_shift_rr || is_shift_imm;
  wire is_right_shift = is_srl || is_sra || is_srli || is_srai;
  wire is_arith_shift = is_sra || is_srai;

  // T-flag comparisons (set T, no register write)
  wire is_cmp_imm = is_clti || is_cltui || is_ceqi;
  wire is_cmp_rr  = is_clt || is_cltu || is_ceq;

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

  // -------------------------------------------------------------------------
  // Temporary register: negedge DFFs with enable.
  //   tmp_lo[7:0]:  captured at E_EXEC_LO
  //   tmp_hi[7:0]:  captured at E_EXEC_HI
  //   carry_r:      DFF (feeds ALU ci_ext)
  //   saved_i_bit:  DFF at negedge (full-period constraint avoids half-period
  //                 path through ALU carry chain → insn_completing → fsm_ready)
  // -------------------------------------------------------------------------
  reg [7:0] tmp_lo;
  reg [7:0] tmp_hi;

  wire is_mem_phase = (state == E_MEM_LO || state == E_MEM_HI);

  // -------------------------------------------------------------------------
  // Bus outputs (state-independent: only depends on memory phase)
  // -------------------------------------------------------------------------
  assign bus_active = is_mem_phase;

  // AB for both E_MEM_LO and E_MEM_HI is just tmp — at E_MEM_LO the
  // sequential block overwrites tmp with tmp+1, so E_MEM_HI reads the
  // incremented address with no carry chain on the critical path.
  always @(*) begin
    ab = 16'bx;
    if (is_mem_phase)
      ab = tmp;
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
    else if (is_rrr || (is_mem_phase && is_rr_load))
      w_sel_mux = {1'b0, ir[13:11]};                           // R-type: rd at [13:11]
    else
      w_sel_mux = {1'b0, ir[7:5]};                             // Default: reg at [7:5]
  end

  // r2_sel: read port 2 register select
  //   Default ir[10:8] works for R,R,R (rs2), R-type stores (data), and R,R loads (dc).
  //   Override to ir[7:5] for I-type stores (data reg in I-type reg field).
  reg [2:0] r2_sel;
  always @(*) begin
    if (is_r9_store || is_sp_store) r2_sel = ir[7:5];
    else                            r2_sel = ir[10:8];
  end
  reg        r2_hi_r;   // dout byte select: 0=r2[7:0], 1=r2[15:8]

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .w_sel  (w_sel_mux),
    .w_data (w_data),
    .w_we   (w_we),
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
  wire [3:0] shamt = is_shift_rr ? r2[3:0] : ir[11:8];

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

  // r1_sel: registered read port 1 select.
  // Set at dispatch (from fetch_ir) and at E_EXEC_HI→E_MEM transitions.
  // Registering removes the instruction decode chain from the critical
  // path (regfile read → ALU → writeback).

  // -------------------------------------------------------------------------
  // Combinational intermediates and next-state values
  // -------------------------------------------------------------------------
  reg        insn_completing;
  reg        jump;
  reg        insn_i_bit;    // Instruction's effect on i_bit (before interrupt override)

  // Next-state values for all DFFs (computed in combinational block)
  reg [2:0]  next_state;
  reg [15:0] next_ir;
  reg        next_carry_r;
  reg [15:1] next_pc;
  reg        next_i_bit;
  reg        next_t_bit;
  reg [1:0]  next_esr;
  reg        next_r2_hi_r;
  // next_mem_carry eliminated
  reg [3:0]  next_r1_sel;

  // Combinational signal for tmp[7:0] DFF at E_EXEC_LO negedge
  reg [7:0]  next_tmp_lo;

  // Interrupt control
  wire fsm_ready = (state == E_IDLE) || insn_completing;
  wire take_nmi = fsm_ready && (nmi_pending || nmi_edge) && !nmi_ack;
  wire take_irq = fsm_ready && !irqb && !insn_i_bit && !take_nmi;
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
    // (mem_carry eliminated)
    next_r1_sel     = r1_sel;

    // --- Output defaults ---
    alu_a           = r1[7:0];
    alu_b           = 8'bx;
    alu_op          = 3'd0;    // ADD
    w_data          = {alu_result, tmp[7:0]};
    w_we            = 1'b0;
    insn_completing = 1'b0;
    jump            = 1'b0;
    insn_i_bit      = i_bit;
    shifter_din     = 15'b0;
    next_tmp_lo     = alu_result;

    case (state)
      E_EXEC_LO: begin
        if (is_reti) begin
          // EPC available on r1[15:0]; no action needed in LO
        end else if (is_int) begin
          // Deferred to E_EXEC_HI
        end else if (is_epcr || is_epcw || is_srr || is_srw) begin
          // Deferred to E_EXEC_HI
        end else if (is_r9_load || is_r9_store || is_sp_load || is_sp_store) begin
          // Address: base + sext(imm8), byte offset (no shift)
          alu_b      = ir[15:8];            // imm8
        end else if (is_auipc) begin
          // pc + (imm8 << 8): lo byte is pc + 0
          alu_a       = {pc[7:1], 1'b0};
          alu_b       = 8'h00;
          next_tmp_lo = alu_result;
        end else if (is_rr_load || is_rr_store) begin
          // Address = rs, no offset
          alu_b      = 8'd0;
        end else if (is_jr_jalr) begin
          // JR/JALR: rs + sext(imm8), no shift
          alu_b      = ir[15:8];            // imm8
          // JR same-page: high byte unchanged, 1 exec cycle
          if (is_jr && (alu_co == ir[15])) begin
            jump            = 1'b1;
            next_pc         = {r1[15:8], alu_result[7:1]};
            insn_completing = 1'b1;
          end
        end else if (is_addi) begin
          alu_b       = ir[15:8];            // imm8
          next_tmp_lo = alu_result;
        end else if (is_li) begin
          next_tmp_lo = ir[15:8];            // imm8
        end else if (is_alu_rrr) begin
          alu_op      = is_alu1 ? {1'b0, ir[15:14]} : 3'd4;  // ALU1: mapped, ALU2(XOR): 4
          alu_b       = r2[7:0];
          next_tmp_lo = alu_result;
        end else if (is_andi) begin
          alu_op      = 3'd2;
          alu_b       = ir[15:8];            // imm8 (sign-extended in HI)
          next_tmp_lo = alu_result;
        end else if (is_ori) begin
          alu_op      = 3'd3;
          alu_b       = ir[15:8];
          next_tmp_lo = alu_result;
        end else if (is_xori) begin
          alu_op      = 3'd4;
          alu_b       = ir[15:8];
          next_tmp_lo = alu_result;
        end else if (is_ceqi) begin
          alu_op      = 3'd4;
          alu_b       = ir[15:8];
          next_tmp_lo = alu_result;
        end else if (is_clti || is_cltui) begin
          alu_op     = 3'd1;                // SUB
          alu_b      = ir[15:8];            // imm8 low byte
          // No write — just save borrow for E_EXEC_HI
        end else if (is_clt || is_cltu) begin
          alu_op     = 3'd1;                // SUB
          alu_b      = r2[7:0];
          // No write — just save borrow for E_EXEC_HI
        end else if (is_ceq) begin
          alu_op      = 3'd4;               // XOR
          alu_b       = r2[7:0];
          next_tmp_lo = alu_result;
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
          next_tmp_lo = 8'h00;           // lo byte = 0
        end else if (is_branch) begin
          // BZ/BNZ: ×2 format, pc + sext(imm8) << 1
          alu_a      = {pc[7:1], 1'b0};
          alu_b      = {ir[14:8], 1'b0};  // imm8[6:0] << 1
          // Same-page taken: high byte unchanged, 1 exec cycle (3 total)
          if ((!(|r1) ^ is_bnz) && (alu_co == ir[15])) begin
            jump            = 1'b1;
            next_pc         = {pc[15:8], alu_result[7:1]};
            insn_completing = 1'b1;
          end
        end else if (is_t_branch) begin
          alu_a      = {pc[7:1], 1'b0};
          alu_b      = {ir[14:8], 1'b0};     // imm8[6:0] << 1
          // Same-page taken: high byte unchanged, 1 exec cycle (3 total)
          if ((t_bit ^ is_bf) && (alu_co == ir[15])) begin
            jump            = 1'b1;
            next_pc         = {pc[15:8], alu_result[7:1]};
            insn_completing = 1'b1;
          end
        end else if (is_jump_imm) begin
          // J/JAL: ×2 format, shares {ir[14:8], 1'b0} with all branch formats
          alu_a      = {pc[7:1], 1'b0};
          alu_b      = {ir[14:8], 1'b0};     // imm10[6:0] << 1
          // J same-page (small offset): high byte unchanged, 1 exec cycle
          if (is_j && ir[7:6] == {2{ir[15]}} && (alu_co == ir[15])) begin
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
          alu_b      = {8{ir[15]}};
          if (!mem_is_store)
            next_r1_sel = {1'b0, ir[7:5]};  // data reg readback for loads
          next_state = E_MEM_LO;
        end else if (is_auipc) begin
          alu_a           = pc[15:8];
          alu_b           = ir[15:8];            // imm8 = upper byte directly
          w_we            = 1'b1;
          insn_completing = 1'b1;
          next_state      = E_IDLE;
        end else if (is_rr_load || is_rr_store) begin
          // Address high byte: carry propagation only
          alu_a      = r1[15:8];
          alu_b      = 8'd0;
          if (!mem_is_store)
            next_r1_sel = {1'b0, ir[13:11]};  // R-type load dest readback
          next_state = E_MEM_LO;
        end else if (is_jr_jalr) begin
          // JR/JALR high byte: sign-extend imm bit 7
          alu_a           = r1[15:8];
          alu_b           = {8{ir[15]}};
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
            insn_i_bit = esr[1];
            next_t_bit = esr[0];
          end else if (is_int) begin
            w_data  = {pc, 1'b0};             // EPC = clean return address
            w_we    = 1'b1;
            jump    = 1'b1;
            next_pc = {13'b0, ir[7:6] + 2'd1};  // vector at ir[7:6]
            next_esr = {saved_i_bit, t_bit};
          end else begin
          // Execute high byte: completes this cycle
          insn_completing = 1'b1;
          if (is_addi) begin
            alu_a      = r1[15:8];
            alu_b      = {8{ir[15]}};           // sign-extend imm bit 7
            w_we       = 1'b1;
          end else if (is_li) begin
            w_data = {{8{ir[15]}}, tmp[7:0]};  // sign-extend imm bit 7
            w_we   = 1'b1;
          end else if (is_alu_rrr) begin
            alu_a      = r1[15:8];
            alu_op     = is_alu1 ? {1'b0, ir[15:14]} : 3'd4;
            alu_b      = r2[15:8];
            w_we       = 1'b1;
          end else if (is_andi) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd2;
            alu_b      = {8{ir[15]}};           // sign-extend
            w_we       = 1'b1;
          end else if (is_ori) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd3;
            alu_b      = {8{ir[15]}};           // sign-extend
            w_we       = 1'b1;
          end else if (is_xori) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd4;
            alu_b      = {8{ir[15]}};           // sign-extend
            w_we       = 1'b1;
          end else if (is_ceqi) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd4;
            alu_b      = {8{ir[15]}};           // sign-extend imm8 bit 7
            // T = (result == 0): no bits set in lo or hi
            next_t_bit = ~((|tmp[7:0]) || (|alu_result));
          end else if (is_clti) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd1;
            alu_b      = {8{ir[15]}};           // sign-extend imm8 bit 7
            next_t_bit = (r1[15] ^ ir[15]) ? r1[15] : alu_result[7];
          end else if (is_cltui) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd1;
            alu_b      = {8{ir[15]}};           // sign-extend for unsigned comparison
            next_t_bit = ~alu_co;
          end else if (is_clt || is_cltu) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd1;
            alu_b      = r2[15:8];
            if (is_cltu)
              next_t_bit = ~alu_co;
            else
              next_t_bit = (r1[15] ^ r2[15]) ? r1[15] : alu_result[7];
          end else if (is_ceq) begin
            alu_a      = r1[15:8];
            alu_op     = 3'd4;
            alu_b      = r2[15:8];
            next_t_bit = ~((|tmp[7:0]) || (|alu_result));
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
            w_data = {ir[15:8], tmp[7:0]};  // imm8 IS the upper byte
            w_we   = 1'b1;
          end else if (is_branch) begin
            // BZ/BNZ HI: sign-extend
            alu_a      = pc[15:8];
            alu_b      = {8{ir[15]}};           // sign-extend
            // BZ/BNZ: full 16-bit zero check (r1[15:0] stable, no write in LO)
            if (!(|r1) ^ is_bnz) begin
              jump    = 1'b1;
              next_pc = {alu_result, tmp[7:1]};
            end
          end else if (is_t_branch) begin
            alu_a      = pc[15:8];
            alu_b      = {8{ir[15]}};            // sign-extend
            if (t_bit ^ is_bf) begin
              jump    = 1'b1;
              next_pc = {alu_result, tmp[7:1]};
            end
          end else if (is_jump_imm) begin
            // J/JAL HI: sext(imm10[9:7]) = {6{sign}, imm[8], imm[7]}
            alu_a      = pc[15:8];
            alu_b      = {{6{ir[15]}}, ir[7], ir[6]};  // sext(imm10[9:7])
            jump       = 1'b1;
            next_pc    = {alu_result, tmp[7:1]};
            if (is_jal) begin
              w_data = {pc, 1'b0};
              w_we   = 1'b1;
            end
          end else if (is_epcr || is_epcw) begin
            w_data = r1;
            w_we   = 1'b1;
          end else if (is_srr) begin
            w_data = {14'b0, i_bit, t_bit};
            w_we   = 1'b1;
          end
          if (is_sei) insn_i_bit = 1'b1;
          if (is_cli) insn_i_bit = 1'b0;
          if (is_srw) begin
            insn_i_bit = r1[1];
            next_t_bit = r1[0];
          end
          end
          next_state = E_IDLE;
        end
        next_i_bit = insn_i_bit;
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
      next_ir    = {8'b11000000, !take_nmi, 1'b0, 1'b0, 5'b11111};
      next_i_bit = 1'b1;
      next_r1_sel = 4'd0;  // INT doesn't read r1; don't care
      next_state = E_EXEC_LO;
    end

    // -----------------------------------------------------------------
    // Instruction dispatch (overrides everything)
    // -----------------------------------------------------------------
    if (ir_accept) begin
      next_pc    = pc + 15'd1;
      next_ir    = fetch_ir;
      next_r2_hi_r = 1'b0;
      if (fetch_ir[4:0] == 5'd31 && fetch_ir[15:14] == 2'b11)
        next_i_bit = 1'b1;
      // Pre-compute r1_sel for execute phase from incoming instruction.
      // Not timing-critical: computed during E_IDLE, registered at negedge.
      if ((fetch_ir[4:0] == 5'd31 && fetch_ir[15:8] == 8'h03)     // RETI
          || (fetch_ir[4:0] == 5'd31 && fetch_ir[15:8] == 8'h10)) // EPCR
        next_r1_sel = 4'd8;                                        // EPC
      else if (fetch_ir[4:0] == 5'd2                               // LW
            || fetch_ir[4:0] == 5'd3                               // LB
            || fetch_ir[4:0] == 5'd4                               // LBU
            || fetch_ir[4:0] == 5'd5                               // SW
            || fetch_ir[4:0] == 5'd6)                              // SB
        next_r1_sel = 4'd0;                                        // R0 base
      else if (fetch_ir[4:0] == 5'd17                              // LW.S
            || fetch_ir[4:0] == 5'd18                              // LB.S
            || fetch_ir[4:0] == 5'd19                              // LBU.S
            || fetch_ir[4:0] == 5'd20                              // SW.S
            || fetch_ir[4:0] == 5'd21)                             // SB.S
        next_r1_sel = 4'd7;                                        // SP (R7)
      else
        next_r1_sel = {1'b0, fetch_ir[7:5]};                      // Default: reg at [7:5]
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
      tmp_lo    <= 8'h00;
      tmp_hi    <= 8'h00;
      r1_sel    <= 4'd0;
    end else begin
      state     <= next_state;
      ir        <= next_ir;
      carry_r   <= next_carry_r;
      pc        <= next_pc;
      i_bit     <= next_i_bit;
      t_bit     <= next_t_bit;
      esr       <= next_esr;
      r2_hi_r   <= next_r2_hi_r;
      // (mem_carry eliminated)
      r1_sel    <= next_r1_sel;
      if (state == E_EXEC_LO) tmp_lo <= next_tmp_lo;
      else if (state == E_MEM_LO) tmp_lo <= tmp_lo + 8'd1;
      if (state == E_EXEC_HI) tmp_hi <= alu_result;
      else if (state == E_MEM_LO) tmp_hi <= tmp_hi + {7'd0, &tmp_lo};
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
      saved_i_bit <= insn_i_bit;
  end

endmodule
