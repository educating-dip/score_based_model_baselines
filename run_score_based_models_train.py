import os
import yaml
import torch
import functools

from datetime import datetime
from src import (get_standard_sde, score_model_simple_trainer, get_standard_score, get_standard_configs, get_standard_train_dataset)

#from configs.disk_ellipses_configs import get_config
from configs.lodopab_vpsde_configs import get_config

def coordinator():

	config = get_config()
	sde = get_standard_sde(config=config)
	score = get_standard_score(config=config, sde=sde, use_ema=False, load_model=False)
	log_dir = '/localdata/AlexanderDenker/score_based_baseline/LoDoPabCT/checkpoints/' + datetime.now().strftime('%Y_%m_%d_%H:%m')

	if not os.path.exists(log_dir):
		os.makedirs(log_dir)
	with open(os.path.join(log_dir,'report.yaml'), 'w') as file:
		yaml.dump(config, file)

	train_dl = get_standard_train_dataset(config)
	score_model_simple_trainer(
			score=score.to(config.device),
			sde=sde,
			train_dl=train_dl,
			optim_kwargs={
					'epochs': config.training.epochs,
					'lr': config.training.lr,
					'ema_warm_start_steps': config.training.ema_warm_start_steps,
					'log_freq': config.training.log_freq,
					'ema_decay': config.training.ema_decay
				},
			val_kwargs={
					'batch_size': config.validation.batch_size,
					'num_steps': config.validation.num_steps,
					'snr': config.validation.snr,
					'eps': config.validation.eps,
					'sample_freq' : config.validation.sample_freq
				},
		device=config.device,
		log_dir=log_dir
		)

if __name__ == '__main__':
	coordinator()
