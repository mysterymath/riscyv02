# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v riscyv02_regfile.v riscyv02_fetch.v riscyv02_alu.v riscyv02_shifter.v riscyv02_execute.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
COMPILE_ARGS    += -DFUNCTIONAL
VERILOG_SOURCES += $(SRC_DIR)/latch_sim.v
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test_memory,test_alu,test_control,test_interrupts,test_cycles

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

PROJECT_DIR = $(CURDIR)/..
CLOCK_PERIOD ?= 14
TT_VENV ?= $(HOME)/ttsetup/venv

.PHONY: harden metrics

harden:
	cd $(PROJECT_DIR) && \
	  python3 -c "import json; c=json.load(open('src/config.json')); c['CLOCK_PERIOD']=$(CLOCK_PERIOD); c['PNR_SDC_FILE']='dir::timing.sdc'; c['SIGNOFF_SDC_FILE']='dir::timing.sdc'; json.dump(c,open('src/config.json','w'),indent=2)" && \
	  . $(TT_VENV)/bin/activate && \
	  python ./tt/tt_tool.py --ihp --create-user-config && \
	  python ./tt/tt_tool.py --ihp --harden ; \
	  git checkout src/config.json

metrics:
	@python3 metrics.py
	@python3 transistor_count.py

clean::
	rm -rf sim_build results.xml
