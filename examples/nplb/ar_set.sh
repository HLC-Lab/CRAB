#!/usr/bin/env bash
# Set the adaptive routing bit of the NICs on the given nodes (AR, the NIC side), then read it back.
#   examples/nplb/ar_set.sh VALUE HOST...     (VALUE is 0 or 1)
# Per host: ssh, list the mlx5_* devices with an ACTIVE port, write the adaptive_routing field of
# ROCE_ACCL with mlxreg (spelled adaptive_routing_forced_en or adaptive_routing_force_en depending
# on the mlxreg version: the spelling in the device's -get output is used), read the register again
# and print "<host> <dev> <pci> AR=<read-back> OK|FAIL" per device. The selective repeat field is
# not touched.
# Exit 0 when every device reads back VALUE, 1 when one does not or a host failed, 2 on bad usage.
# Not persistent across reboots. Companion of sr_set.sh and sr_state.sh: it runs sr_set.sh with
# ACCL_BIT=ar, so the env of sr_set.sh (SR_SSH, MLXREG, SR_SYSFS) applies here too.
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ACCL_BIT=ar ACCL_USAGE_FROM="${BASH_SOURCE[0]}" exec "$here/sr_set.sh" "$@"
