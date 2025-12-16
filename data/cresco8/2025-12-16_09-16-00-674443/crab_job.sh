#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/cresco8/2025-12-16_09-16-00-674443/slurm_output.log
#SBATCH --error=./data/cresco8/2025-12-16_09-16-00-674443/slurm_error.log
#SBATCH --nodes=128
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --dependency=afterany:1924702
#SBATCH --partition=cresco8_cpu
#SBATCH --account=enea
#SBATCH --time=01:00:00

source /afs/enea.it/por/user/piarulli/CRAB/.venv/bin/activate

/afs/enea.it/por/user/piarulli/CRAB/.venv/bin/python /afs/enea.it/por/user/piarulli/CRAB/cli.py --worker --workdir ./data/cresco8/2025-12-16_09-16-00-674443
