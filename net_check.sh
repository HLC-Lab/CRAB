#!/usr/bin/env bash
# Read-only: what can this shell fetch through the proxy, and is the toolchain there to build Open MPI from a git
# checkout? Downloads nothing. Run it on node A in a shell that has the proxy (. ~/crab/CRAB/examples/nplb/env.sh):
#   bash ~/crab/lab/net_check.sh
# Prints one line per probe and a verdict for ompi_cuda_build.sh.
set -u
V=${OMPI_VER:-4.1.6}
probe() { # name url
    local code
    code=$(curl -s -o /dev/null -I -L -m 25 -w '%{http_code}' "$2" 2>/dev/null)
    echo "$1: HTTP ${code:-none}"
    [[ $code == 200 ]]
}
echo "proxy: ${https_proxy:-${http_proxy:-NOT SET (source examples/nplb/env.sh)}}"
probe "open-mpi.org tarball" "https://download.open-mpi.org/release/open-mpi/v${V%.*}/openmpi-$V.tar.bz2" && T1=1 || T1=0
probe "github release tarball" "https://github.com/open-mpi/ompi/releases/download/v$V/openmpi-$V.tar.bz2" && T2=1 || T2=0
git ls-remote https://github.com/open-mpi/ompi "v${V%.*}.x" 2>/dev/null | head -1 | grep -q . && G=1 || G=0
echo "github git clone (ls-remote v${V%.*}.x): $([[ $G == 1 ]] && echo yes || echo no)"
miss=""
for t in autoconf automake libtool flex perl m4 make gcc; do command -v "$t" >/dev/null || miss="$miss $t"; done
echo "build tools missing:${miss:- none}"
if [[ $T1 == 1 ]]; then echo "VERDICT: the open-mpi.org tarball works, ompi_cuda_build.sh as it is"
elif [[ $T2 == 1 ]]; then echo "VERDICT: only the GitHub release tarball works (tell Claude: tarball from GitHub)"
elif [[ $G == 1 && -z $miss ]]; then echo "VERDICT: git clone + autogen.pl possible (tell Claude: use git)"
else echo "VERDICT: no way to fetch it here, Claude puts the tarball into the nplb-lab branch"
fi
