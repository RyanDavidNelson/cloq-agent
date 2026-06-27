# Build NEORV32 firmware. Requires the NEORV32 software framework + a RISC-V GCC
# (e.g. the xpack riscv-none-elf-gcc). Set NEORV32_HOME to your neorv32 checkout.
NEORV32_HOME ?= ../../vendor/neorv32
APP_SRC = measure_stub.c
# point this at the compiled target object (one of eval/targets/*.o)
TARGET_OBJ ?= target_function.o
include $(NEORV32_HOME)/sw/common/common.mk

# `make TARGET_OBJ=ct_swap.o` builds the measurement image for that target.
