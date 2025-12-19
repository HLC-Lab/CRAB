#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/cresco8/2025-12-19_01-00-19-649664/slurm_output.log
#SBATCH --error=./data/cresco8/2025-12-19_01-00-19-649664/slurm_error.log
#SBATCH --nodes=256
#SBATCH --ntasks-per-node=32
#SBATCH --exclusive
#SBATCH --dependency=afterany:1956746
#SBATCH --partition=cresco8_cpu
#SBATCH --account=ssheneaadm
#SBATCH --qos=ssheneaadm
#SBATCH --time=01:00:00

source /afs/enea.it/fra/user/faltelli/CRAB/.venv/bin/activate

/afs/enea.it/fra/user/faltelli/CRAB/.venv/bin/python /afs/enea.it/fra/user/faltelli/CRAB/cli.py --worker --workdir ./data/cresco8/2025-12-19_01-00-19-649664
