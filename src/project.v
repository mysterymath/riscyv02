/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 *
 * RISCY-V02 "Byte Byte Jump" — Minimal Turing-Complete RISC-V Subset
 *
 * ISA: LW, SW, JR only (all other opcodes = NOP).
 * Architecture: 2-stage pipeline (Fetch / Execute) with 8-bit muxed bus.
 *
 * Bus protocol: identical to tt_um_arlet_6502 mux/demux protocol.
 *
 *   mux_sel=0 (address out):
 *     uo_out[7:0]  = AB[7:0]
 *     uio_out[7:0] = AB[15:8]   (uio_oe = 8'hFF, all output)
 *
 *   mux_sel=1 (data + status):
 *     uo_out[0]    = RWB
 *     uo_out[1]    = SYNC (always 0 for this core)
 *     uo_out[7:2]  = 0
 *     uio[7:0]     = D[7:0] bidirectional data bus
 *     uio_oe       = RWB ? 8'h00 : 8'hFF
 *
 * Instruction encoding (16-bit):
 *   LW  rd, off(rs1):  [1000][rs1:3][off6:6][rd:3]
 *   SW  rs2, off(rs1): [1010][rs1:3][off6:6][rs2:3]
 *   JR  rs, off6:      [1011100][off6:6][rs:3]
 */

`default_nettype none

module tt_um_riscyv02 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

  // -----------------------------------------------------------------------
  // Mux select: dual-edge register (identical to 6502 wrapper).
  //
  // mux_sel tracks clk, delayed by tCQ to provide hold time at each
  // transition.  Two FFs + XOR decompose a dual-edge toggle:
  //   posedge: if (mux_sel==0) toggle q   → mux_sel becomes 1
  //   negedge: if (mux_sel==1) toggle q_d → mux_sel becomes 0
  // -----------------------------------------------------------------------
  wire mux_sel = q ^ q_d;

  reg q;
  always @(posedge clk or negedge rst_n)
    if (!rst_n)        q <= 1'b0;
    else if (!mux_sel) q <= ~q;

  reg q_d;
  always @(negedge clk or negedge rst_n)
    if (!rst_n)       q_d <= 1'b0;
    else if (mux_sel) q_d <= ~q_d;

  // -----------------------------------------------------------------------
  // Internal bus signals
  // -----------------------------------------------------------------------
  wire [15:0] AB;
  wire [7:0]  DO;
  wire        RWB;

  // -----------------------------------------------------------------------
  // Register file: 8 × 16-bit GP registers
  // -----------------------------------------------------------------------
  reg [15:0] regs [0:7];

  // -----------------------------------------------------------------------
  // Fetch FSM
  //
  // States: F_LO → F_HI → F_LO → ...
  //
  // F_LO: address bus carries PC (low byte address).  At negedge,
  //        capture uio_in into ir_lo, advance to F_HI.
  // F_HI: address bus carries PC|1 (high byte address).  At negedge,
  //        assemble {uio_in, ir_lo} into new_ir, signal ir_valid,
  //        advance PC by 2, return to F_LO.
  // -----------------------------------------------------------------------
  localparam F_LO = 1'b0;
  localparam F_HI = 1'b1;

  reg        f_state;
  reg [15:0] PC;
  reg [7:0]  ir_lo;    // low byte of instruction, captured in F_LO

  // -----------------------------------------------------------------------
  // Execute FSM
  // -----------------------------------------------------------------------
  localparam E_IDLE     = 3'd0;
  localparam E_LOAD_LO  = 3'd1;
  localparam E_LOAD_HI  = 3'd2;
  localparam E_STORE_LO = 3'd3;
  localparam E_STORE_HI = 3'd4;

  reg [2:0]  e_state;
  reg [15:0] IR;       // instruction register (captured by execute for LW/SW)
  reg [15:0] MAR;      // memory address register
  reg [7:0]  mem_lo;   // low byte captured during word load (E_LOAD_LO)

  wire exec_busy = (e_state != E_IDLE);

  // -----------------------------------------------------------------------
  // Fetch → Execute communication
  // -----------------------------------------------------------------------
  reg        ir_valid;
  reg [15:0] new_ir;

  // Execute → Fetch communication
  reg        pc_load;
  reg [15:0] pc_target;

  // -----------------------------------------------------------------------
  // Execute bus outputs
  // -----------------------------------------------------------------------
  reg [15:0] exec_ab;
  reg [7:0]  exec_do;
  reg        exec_rwb;

  // -----------------------------------------------------------------------
  // Datapath: adder and sign-extended offset
  // -----------------------------------------------------------------------
  wire [15:0] sext_off_x2 = {{9{new_ir[8]}}, new_ir[8:3], 1'b0};

  // A-input mux: LW/SW use rs1 = new_ir[11:9], JR uses rs = new_ir[2:0]
  wire [2:0] a_sel = (new_ir[15:12] == 4'b1000 || new_ir[15:12] == 4'b1010)
                     ? new_ir[11:9] : new_ir[2:0];
  wire [15:0] adder_result = regs[a_sel] + sext_off_x2;

  // -----------------------------------------------------------------------
  // Instruction decode (from new_ir)
  // -----------------------------------------------------------------------
  wire is_lw = (new_ir[15:12] == 4'b1000);
  wire is_sw = (new_ir[15:12] == 4'b1010);
  wire is_jr = (new_ir[15:9]  == 7'b1011100);

  // -----------------------------------------------------------------------
  // Fetch FSM address output
  // -----------------------------------------------------------------------
  wire [15:0] fetch_ab = (f_state == F_HI) ? {PC[15:1], 1'b1} : PC;

  // -----------------------------------------------------------------------
  // Bus arbitration
  // -----------------------------------------------------------------------
  assign AB  = exec_busy ? exec_ab  : fetch_ab;
  assign RWB = exec_busy ? exec_rwb : 1'b1;
  assign DO  = exec_do;

  // -----------------------------------------------------------------------
  // Execute bus address/data/control (combinational from state + regs)
  // -----------------------------------------------------------------------
  always @(*) begin
    exec_ab  = MAR;
    exec_do  = 8'h00;
    exec_rwb = 1'b1;
    case (e_state)
      E_LOAD_LO: begin
        exec_ab  = MAR;
        exec_rwb = 1'b1;
      end
      E_LOAD_HI: begin
        exec_ab  = {MAR[15:1], 1'b1};
        exec_rwb = 1'b1;
      end
      E_STORE_LO: begin
        exec_ab  = MAR;
        exec_do  = regs[IR[2:0]][7:0];
        exec_rwb = 1'b0;
      end
      E_STORE_HI: begin
        exec_ab  = {MAR[15:1], 1'b1};
        exec_do  = regs[IR[2:0]][15:8];
        exec_rwb = 1'b0;
      end
      default: begin
        exec_ab  = 16'h0000;
        exec_rwb = 1'b1;
      end
    endcase
  end

  // -----------------------------------------------------------------------
  // Fetch FSM (negedge clk)
  // -----------------------------------------------------------------------
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      f_state  <= F_LO;
      PC       <= 16'h0000;
      ir_lo    <= 8'h00;
      ir_valid <= 1'b0;
      new_ir   <= 16'h0000;
    end else begin
      ir_valid <= 1'b0;  // default: pulse off

      if (pc_load) begin
        PC      <= pc_target;
        f_state <= F_LO;
      end else if (!exec_busy) begin
        case (f_state)
          F_LO: begin
            ir_lo   <= uio_in;
            f_state <= F_HI;
          end
          F_HI: begin
            new_ir   <= {uio_in, ir_lo};
            ir_valid <= 1'b1;
            PC       <= {PC[15:1] + 15'd1, 1'b0};
            f_state  <= F_LO;
          end
          default: f_state <= F_LO;
        endcase
      end
    end
  end

  // -----------------------------------------------------------------------
  // Execute FSM (negedge clk)
  // -----------------------------------------------------------------------
  integer k;
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      e_state   <= E_IDLE;
      IR        <= 16'h0000;
      MAR       <= 16'h0000;
      mem_lo    <= 8'h00;
      pc_load   <= 1'b0;
      pc_target <= 16'h0000;
      for (k = 0; k < 8; k = k + 1)
        regs[k] <= 16'h0000;
    end else begin
      pc_load <= 1'b0;  // default: pulse off

      case (e_state)
        E_IDLE: begin
          if (ir_valid) begin
            if (is_jr) begin
              pc_load   <= 1'b1;
              pc_target <= adder_result;
            end else if (is_lw) begin
              MAR     <= adder_result;
              IR      <= new_ir;
              e_state <= E_LOAD_LO;
            end else if (is_sw) begin
              MAR     <= adder_result;
              IR      <= new_ir;
              e_state <= E_STORE_LO;
            end
            // else NOP: do nothing
          end
        end

        E_LOAD_LO: begin
          mem_lo  <= uio_in;
          e_state <= E_LOAD_HI;
        end

        E_LOAD_HI: begin
          regs[IR[2:0]] <= {uio_in, mem_lo};
          e_state       <= E_IDLE;
        end

        E_STORE_LO: begin
          e_state <= E_STORE_HI;
        end

        E_STORE_HI: begin
          e_state <= E_IDLE;
        end

        default: e_state <= E_IDLE;
      endcase
    end
  end

  // -----------------------------------------------------------------------
  // Output muxes (identical protocol to 6502 wrapper)
  // -----------------------------------------------------------------------
  assign uo_out = mux_sel ? {6'b0, 1'b0, RWB} : AB[7:0];
  //                              SYNC=0 ^

  assign uio_out = mux_sel ? DO : AB[15:8];

  assign uio_oe = mux_sel ? (RWB ? 8'h00 : 8'hFF) : 8'hFF;

  // Unused
  wire _unused = &{ena, ui_in, 1'b0};

endmodule
