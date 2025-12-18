#!/bin/bash

#SBATCH --job-name=crab_All-to-All
#SBATCH --output=./data/lumi/2025-12-18_23-26-02-775306/slurm_output.log
#SBATCH --error=./data/lumi/2025-12-18_23-26-02-775306/slurm_error.log
#SBATCH --nodes=16
#SBATCH --ntasks-per-node=32
#SBATCH --exclusive
#SBATCH --dependency=afterany:15435461
#SBATCH --partition=standard-g
#SBATCH --account=project_465001736
#SBATCH --time=01:00:00

source /pfs/lustrep4/users/pasqualo/CRAB/.venv/bin/activate

/opt/cray/pe/python/3.11.7/bin/python /pfs/lustrep4/users/pasqualo/CRAB/cli.py --worker --workdir ./data/lumi/2025-12-18_23-26-02-775306
