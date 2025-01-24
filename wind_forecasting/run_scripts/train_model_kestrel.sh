#!/bin/bash 
#SBATCH --account=ssc
#SBATCH --time=12:00:00
#SBATCH --output=%j-%x.log
##SBATCH --partition=debug
#SBATCH --nodes=1 # this needs to match Trainer(num_nodes...)
#SBATCH --gres=gpu:1
#SBATCH --ntasks-per-node=1 # this needs to match Trainer(devices=...)
#SBATCH --mem-per-cpu=85G

##SBATCH --mem=0 # refers to CPU (not GPU) memory, automatically given all GPU memory in a SLURM job, 85G
##SBATCH --ntasks=1

# salloc --account=ssc --time=01:00:00 --gpus=2 --ntasks-per-node=2 --partition=debug

module purge
ml PrgEnv-intel
ml mamba
mamba activate wind_forecasting
#cd /home/ahenry/toolboxes/wind_forecasting_env/wind-forecasting/wind_forecasting/models/pytorch-transformer-ts/informer

echo "SLURM_NTASKS=${SLURM_NTASKS}"
echo "SLURM_JOB_NUM_NODES=${SLURM_JOB_NUM_NODES}"
echo "SLURM_GPUS_ON_NODE=${SLURM_GPUS_ON_NODE}"
echo "SLURM_JOB_GPUS=${SLURM_JOB_GPUS}"
echo "SLURM_JOB_GRES=${SLURM_JOB_GRES}"

srun python train_model.py --config ../../examples/inputs/training_inputs_kestrel.yaml --model $1
# srun python informer.py
#python train_spacetimeformer.py spacetimeformer windfarm --debug --run_name spacetimeformer_windfarm_debug --context_points 600 --target_points 600

