#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/cresco8/2025-12-15_09-44-03-346089/slurm_output.log
#SBATCH --error=./data/cresco8/2025-12-15_09-44-03-346089/slurm_error.log
#SBATCH --nodes=40
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --dependency=afterany:1907575
#SBATCH --partition=cresco8_cpu
#SBATCH --account=enea
#SBATCH --time=01:00:00

source /afs/enea.it/por/user/piarulli/CRAB/.venv/bin/activate

/afs/enea.it/por/user/piarulli/CRAB/.venv/bin/python /afs/enea.it/por/user/piarulli/CRAB/cli.py --worker --workdir ./data/cresco8/2025-12-15_09-44-03-346089
