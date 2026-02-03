/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// =========================================================================
// Execute unit: execute FSM + 8-bit ALU + decode (registers owned by top)
// =========================================================================
module riscyv02_execute (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        ir_valid,
    input  wire [15:0] new_ir,
    input  wire [15:0] exec_r,
    output wire        busy,
    output wire        bus_active,
    output reg  [15:0] ab,
    output reg  [7:0]  dout,
    output reg         rwb,
    // Register file interface (combinational)
    output wire [2:0]  exec_r_sel,
    output wire [2:0]  w_sel,
    output wire [15:0] w_data,
    output wire        w_we,
    output wire        ir_accept,
    output wire        w_pending,
    output wire [2:0]  w_pending_sel
);

  localparam E_IDLE       = 3'd0;
  localparam E_LOAD_LO    = 3'd1;
  localparam E_LOAD_HI    = 3'd2;
  localparam E_STORE_LO   = 3'd3;
  localparam E_STORE_HI   = 3'd4;
  localparam E_LOAD_ADDR  = 3'd5;
  localparam E_STORE_ADDR = 3'd6;

  reg [2:0]  state;
  reg [15:0] IR;
  reg [15:0] MAR;
  reg [7:0]  mem_lo;
  reg [7:0]  store_hi;    // high byte of store data, latched in E_STORE_LO

  // Ready states can accept a new dispatch.  ADDR states are not ready
  // (they still need the exec_r port for the high-byte ALU) but they don't
  // block fetch F_HI, so ir_valid can arrive in time for the next ready state.
  wire ready = (state == E_IDLE) || (state == E_LOAD_HI) || (state == E_STORE_HI);
  assign busy       = bus_active;
  assign bus_active = (state == E_LOAD_LO || state == E_LOAD_HI ||
                       state == E_STORE_LO || state == E_STORE_HI);

  // ALU instance
  wire [7:0] alu_a, alu_b;
  wire       alu_start;
  wire [7:0] alu_result;
  wire       alu_co;

  riscyv02_alu u_alu (
    .clk    (clk),
    .rst_n  (rst_n),
    .a      (alu_a),
    .b      (alu_b),
    .start  (alu_start),
    .result (alu_result),
    .co     (alu_co)
  );

  // ALU input mux
  assign alu_start = ready;
  assign alu_b = (state == E_LOAD_ADDR || state == E_STORE_ADDR)
                 ? {8{IR[8]}}
                 : {new_ir[8], new_ir[8:3], 1'b0};

  // Forwarding: when dispatching from E_LOAD_HI, the regfile write hasn't
  // propagated yet.  Bypass w_data when the new instruction's rs1 matches
  // the load destination.
  wire exec_fwd = (state == E_LOAD_HI) && ir_valid && (new_ir[11:9] == IR[2:0]);

  assign alu_a = (state == E_LOAD_ADDR || state == E_STORE_ADDR)
                 ? exec_r[15:8]
                 : exec_fwd ? w_data[7:0] : exec_r[7:0];

  // Instruction decode
  wire is_lw = (new_ir[15:12] == 4'b1000);
  wire is_sw = (new_ir[15:12] == 4'b1010);

  // Ready states consume ir_valid (even for NOPs / unrecognised instructions).
  assign ir_accept = ready && ir_valid;

  // Write-pending tracking: execute holds a LW whose register write hasn't
  // fired yet.  Exported so project.v can detect RAW hazards against fetch.
  wire is_lw_ir = (IR[15:12] == 4'b1000);
  assign w_pending     = (state == E_LOAD_ADDR || state == E_LOAD_LO) && is_lw_ir;
  assign w_pending_sel = IR[2:0];

  // Dispatch: recognised instruction that execute will act on.
  wire dispatch_valid = ir_valid && (is_lw || is_sw);
  wire [2:0] dispatch_next = is_lw ? E_LOAD_ADDR : E_STORE_ADDR;

  // Register read port mux:
  //   E_STORE_LO:              rs2 from IR (store data)
  //   E_LOAD_ADDR/E_STORE_ADDR: rs1 from IR (high byte for ALU)
  //   Ready / other states:     rs1 from new_ir (dispatch ALU)
  assign exec_r_sel = (state == E_STORE_LO)
                      ? IR[2:0]
                      : (state == E_LOAD_ADDR || state == E_STORE_ADDR)
                      ? IR[11:9]
                      : new_ir[11:9];

  // Combinational register write: fires in E_LOAD_HI
  assign w_we   = (state == E_LOAD_HI);
  assign w_sel  = IR[2:0];
  assign w_data = {uio_in, mem_lo};

  // Execute bus address/data/control (combinational)
  always @(*) begin
    ab   = MAR;
    dout = 8'h00;
    rwb  = 1'b1;
    case (state)
      E_LOAD_LO: begin
        ab  = MAR;
        rwb = 1'b1;
      end
      E_LOAD_HI: begin
        ab  = {MAR[15:1], 1'b1};
        rwb = 1'b1;
      end
      E_STORE_LO: begin
        ab   = MAR;
        dout = exec_r[7:0];
        rwb  = 1'b0;
      end
      E_STORE_HI: begin
        ab   = {MAR[15:1], 1'b1};
        dout = store_hi;
        rwb  = 1'b0;
      end
      default: begin
        ab  = 16'h0000;
        rwb = 1'b1;
      end
    endcase
  end

  // Execute FSM (negedge clk) — only updates private state
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state        <= E_IDLE;
      IR           <= 16'h0000;
      MAR          <= 16'h0000;
      mem_lo       <= 8'h00;
      store_hi     <= 8'h00;
    end else begin
      case (state)
        E_IDLE: begin
          if (dispatch_valid) begin
            MAR[7:0] <= alu_result;
            IR       <= new_ir;
            state    <= dispatch_next;
          end
        end

        E_LOAD_ADDR: begin
          MAR[15:8] <= alu_result;
          state     <= E_LOAD_LO;
        end

        E_STORE_ADDR: begin
          MAR[15:8] <= alu_result;
          state     <= E_STORE_LO;
        end

        E_LOAD_LO: begin
          mem_lo  <= uio_in;
          state   <= E_LOAD_HI;
        end

        E_LOAD_HI: begin
          if (dispatch_valid) begin
            MAR[7:0] <= alu_result;
            IR       <= new_ir;
            state    <= dispatch_next;
          end else begin
            state <= E_IDLE;
          end
        end

        E_STORE_LO: begin
          store_hi <= exec_r[15:8];
          state    <= E_STORE_HI;
        end

        E_STORE_HI: begin
          if (dispatch_valid) begin
            MAR[7:0] <= alu_result;
            IR       <= new_ir;
            state    <= dispatch_next;
          end else begin
            state <= E_IDLE;
          end
        end

        default: state <= E_IDLE;
      endcase
    end
  end

endmodule
