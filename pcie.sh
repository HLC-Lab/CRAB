#!/usr/bin/env bash
# Read-only: the PCIe path from the CPU to each network port and each GPU on this node.
#   bash ~/crab/lab/pcie.sh
# One line per device: the root bus (pciDDDD:BB, one per CPU root complex), then every bridge on the
# way and the device itself, each with its link "speed GT/s x width" (a "!" marks a link running
# below its maximum). Devices that share a bridge share that bridge's link.
# Same information as: readlink -f /sys/class/infiniband/mlx5_N/device
SYS=${LAB_SYSFS:-/sys}

chain() {
    local path=$1 out="" p="" c
    IFS=/ read -ra parts <<<"${path#*/devices/}"
    for c in "${parts[@]}"; do
        p+=/$c
        if [[ $c == pci* ]]; then
            out+="$c"
        elif [[ $c == *:*:*.* ]]; then
            local f=$SYS/devices$p sp w msp mw mark=""
            sp=$(cut -d' ' -f1 "$f/current_link_speed" 2>/dev/null)
            w=$(cat "$f/current_link_width" 2>/dev/null)
            msp=$(cut -d' ' -f1 "$f/max_link_speed" 2>/dev/null)
            mw=$(cat "$f/max_link_width" 2>/dev/null)
            [[ -n $sp && ($sp != "$msp" || $w != "$mw") ]] && mark="!"
            out+=" > ${c#0000:}${sp:+(${sp%.0}x$w$mark)}"
        else
            break
        fi
    done
    echo "$out"
}

echo "$(hostname -s): device: root bus > bridges > device (link GT/s x width, ! = below max)"
for d in "$SYS"/class/infiniband/mlx5_*; do
    [[ -e $d/device ]] || continue
    n=$(basename "$d")
    printf '%-8s %s\n' "$n" "$(chain "$(readlink -f "$d/device")")"
done | sort -V
nvidia-smi --query-gpu=index,pci.bus_id --format=csv,noheader 2>/dev/null | while IFS=", " read -r i bus; do
    b=$(echo "$bus" | tr 'A-F' 'a-f')
    printf '%-8s %s\n' "GPU$i" "$(chain "$(readlink -f "$SYS/bus/pci/devices/${b#0000}")")"
done
