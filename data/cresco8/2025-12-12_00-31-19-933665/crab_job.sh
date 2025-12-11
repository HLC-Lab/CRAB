#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/cresco8/2025-12-12_00-31-19-933665/slurm_output.log
#SBATCH --error=./data/cresco8/2025-12-12_00-31-19-933665/slurm_error.log
#SBATCH --nodes=32
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --partition=cresco8_cpu
#SBATCH --account=enea
#SBATCH --gres=tmpfs:0
#SBATCH --time=01:00:00

module load pfs
module load base
module load intel/oneapi
module load slurm_tools
module load mpi_flavour/openmpi_intel-4.1.7
source /afs/enea.it/por/user/piarulli/CRAB/.venv/bin/activate

/afs/enea.it/por/user/piarulli/CRAB/.venv/bin/python /afs/enea.it/por/user/piarulli/CRAB/cli.py --worker --workdir ./data/cresco8/2025-12-12_00-31-19-933665
