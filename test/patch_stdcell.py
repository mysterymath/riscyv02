#!/usr/bin/env python3
"""Patch IHP sg13g2 stdcell Verilog for Icarus Verilog GL simulation.

The PDK models route sequential cell signals through delayed_* wires
driven by $setuphold timing checks.  Icarus doesn't support the delayed
output feature of timing checks, so those wires stay X forever.

This script adds 'assign delayed_X = X;' after each delayed wire
declaration — the identity transform appropriate for functional (zero-
delay) simulation.  Use with -gno-specify to disable the timing check
model entirely (we have no SDF back-annotation).
"""

import re
import sys

content = sys.stdin.read()

def add_assigns(match):
    line = match.group(0)
    names = re.findall(r'delayed_(\w+)', line)
    assigns = '\n'.join(f'\tassign delayed_{n} = {n};' for n in names)
    return line + '\n' + assigns

sys.stdout.write(re.sub(r'\twire\s+delayed_[^;]+;', add_assigns, content))
