#!/usr/bin/env python3
"""Patch IHP sg13g2 stdcell Verilog for Icarus Verilog GL simulation.

The PDK models route sequential cell signals through delayed_* wires
driven by $setuphold timing checks.  Icarus doesn't support the delayed
output feature of timing checks, so those wires stay X forever.

This script adds 'assign #0 delayed_X = X;' after each delayed wire
declaration, making the models functional under Icarus.

The #0 (inactive-region) delay is critical: without it, the UDP
evaluations happen in the same active region as the clock edge,
racing with testbench @(posedge/negedge clk) blocks that read outputs.
With #0, the sequence is:
  Active:   testbench reads pre-edge outputs
  Inactive: delayed_* signals propagate
  Active:   UDPs evaluate, new outputs settle
  NBA:      testbench register updates commit
This matches RTL semantics where DFF updates use NBA (<=).
"""

import re
import sys

content = sys.stdin.read()

def add_assigns(match):
    line = match.group(0)
    names = re.findall(r'delayed_(\w+)', line)
    assigns = '\n'.join(f'\tassign #0 delayed_{n} = {n};' for n in names)
    return line + '\n' + assigns

sys.stdout.write(re.sub(r'\twire\s+delayed_[^;]+;', add_assigns, content))
