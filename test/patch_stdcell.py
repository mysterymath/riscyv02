#!/usr/bin/env python3
"""Patch IHP sg13g2 stdcell Verilog for Icarus Verilog GL simulation.

The PDK models route sequential cell signals through delayed_* wires
driven by $setuphold timing checks.  Icarus < 13 doesn't drive delayed
outputs from timing checks, so those wires stay X forever.  This script
adds 'assign delayed_X = X;' after each delayed wire declaration.

Icarus >= 13 natively copies delayed signals to their originals.  Adding
explicit assigns on top creates a multi-driver conflict (all outputs X).
Skip patching in that case.

Use with -gno-specify to disable the timing check model entirely.
"""

import re
import subprocess
import sys

content = sys.stdin.read()

# Icarus >= 13 handles delayed signals natively; patching creates
# multi-driver X conflicts.  Pass through unmodified.
try:
    out = subprocess.run(['iverilog', '-V'], capture_output=True, text=True).stdout
    ver = re.search(r'version (\d+)\.', out)
    if ver and int(ver.group(1)) >= 13:
        sys.stdout.write(content)
        sys.exit(0)
except (FileNotFoundError, subprocess.SubprocessError):
    pass

def add_assigns(match):
    line = match.group(0)
    names = re.findall(r'delayed_(\w+)', line)
    assigns = '\n'.join(f'\tassign delayed_{n} = {n};' for n in names)
    return line + '\n' + assigns

sys.stdout.write(re.sub(r'\twire\s+delayed_[^;]+;', add_assigns, content))
