# build_neorv32_zynq.tcl — NEORV32 softcore + Zynq US+ PS on the RealDigital AUP-ZU3.
#
# Produces a bitstream + .xsa whose PL holds a deterministic NEORV32 (caches off, Zicntr on)
# wrapped as an AXI4-Lite peripheral, controlled by the A53 PS. PYNQ on the PS drives it
# (see ../host/measure.py).
#
# Usage:  vivado -mode batch -source build_neorv32_zynq.tcl -tclargs <path-to-neorv32-repo>
#
# Prereqs:
#   * AUP-ZU3 board files installed in Vivado (from RealDigital).
#   * NEORV32 packaged as Vivado IP first:
#       in neorv32/rtl/system_integration run  `source neorv32_vivado_ip.tcl`
#     which creates neorv32_vivado_ip_work/packaged_ip (added below to the IP repo).
#
# CONFIRM the exact part string from the board reference manual / installed board files;
# it is an XCZU3EG in the SFVC784 package — pin the speed grade from the board files.

set neorv32_repo [lindex $argv 0]
if {$neorv32_repo eq ""} { set neorv32_repo "../../vendor/neorv32" }

set part        "xczu3eg-sfvc784-1-e"   ;# VERIFY against board files
set proj        "neorv32_zynq"
set bd          "neorv32_bd"
set outdir      "./build"

file mkdir $outdir
create_project $proj $outdir -part $part -force

# --- NEORV32 IP repository -------------------------------------------------
set ip_repo "$neorv32_repo/rtl/system_integration/neorv32_vivado_ip_work/packaged_ip"
set_property ip_repo_paths $ip_repo [current_project]
update_ip_catalog

# --- block design ----------------------------------------------------------
create_bd_design $bd

# Zynq UltraScale+ MPSoC PS, board-aware preset
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e zynq_ps]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e \
  -config {apply_board_preset "1"} $ps
# expose one PL clock (FCLK) and reset for the NEORV32 domain
set_property -dict [list \
  CONFIG.PSU__FPGA_PL0_ENABLE {1} \
  CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {50} \
] $ps

# NEORV32 IP — deterministic config for clean timing ground truth
set cpu [create_bd_cell -type ip -vlnv NEORV32:user:neorv32_vivado_ip neorv32_0]
# These property names track the NEORV32 IP customization GUI; adjust to your IP version.
catch { set_property -dict [list \
  CONFIG.RISCV_ISA_M {true} \
  CONFIG.RISCV_ISA_C {true} \
  CONFIG.RISCV_ISA_Zicntr {true} \
  CONFIG.ICACHE_EN {false} \
  CONFIG.DCACHE_EN {false} \
  CONFIG.IMEM_EN {true} \
  CONFIG.DMEM_EN {true} \
  CONFIG.IO_MTIME_EN {true} \
] $cpu }

# AXI plumbing: PS master -> NEORV32 (control mailbox) + a BRAM for program/IO if desired
apply_bd_automation -rule xilinx.com:bd_rule:axi4 \
  -config { Master {/zynq_ps/M_AXI_HPM0_FPD} Clk {Auto} } \
  [get_bd_intf_pins neorv32_0/*AXI* ]

# auto-connect clocks/resets
apply_bd_automation -rule xilinx.com:bd_rule:clkrst \
  -config { Clk {/zynq_ps/pl_clk0 (50 MHz)} } [get_bd_pins neorv32_0/clk*]

assign_bd_address
validate_bd_design
save_bd_design

# --- synthesize, implement, write artifacts --------------------------------
make_wrapper -files [get_files $bd.bd] -top
add_files -norecurse "$outdir/$proj.srcs/sources_1/bd/$bd/hdl/${bd}_wrapper.vhd"
set_property top ${bd}_wrapper [current_fileset]

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1

write_hw_platform -fixed -include_bit -force -file "$outdir/${proj}.xsa"
puts "DONE: bitstream + xsa in $outdir"
