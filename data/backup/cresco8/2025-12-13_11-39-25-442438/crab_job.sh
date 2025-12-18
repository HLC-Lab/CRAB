#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/cresco8/2025-12-13_11-39-25-442438/slurm_output.log
#SBATCH --error=./data/cresco8/2025-12-13_11-39-25-442438/slurm_error.log
#SBATCH --nodes=10
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --dependency=afterany:1886965
#SBATCH --partition=cresco8_cpu
#SBATCH --account=enea
#SBATCH --mem=500G
#SBATCH --auks=yes
#SBATCH --time=01:00:00

source /afs/enea.it/por/user/piarulli/CRAB/.venv/bin/activate

/afs/enea.it/por/user/piarulli/CRAB/.venv/bin/python /afs/enea.it/por/user/piarulli/CRAB/cli.py --worker --workdir ./data/cresco8/2025-12-13_11-39-25-442438
