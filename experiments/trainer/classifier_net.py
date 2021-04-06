import torch
import torch.nn.functional as F
import pytorch_lightning as pl
import pytorch_lightning.metrics.functional as metricsF

import sys
import os.path as P
sys.path.insert(0, P.abspath(P.join(P.dirname(__file__), '../../')))
from lib.utils.clip_pad import clip_pad_center


class BinaryClassifierNet(pl.LightningModule):
    def __init__(self, model, loss='BCE', optimizer=None, lr=1e-3, p_dropout=0):
        super().__init__()
        self.model = model

        self.val_accuracy = pl.metrics.Accuracy(compute_on_step=False)
        self.lr = lr
        self.p_dropout = p_dropout
        if loss == 'dice':
            from .losses import binary_dice_loss
            self.loss_f = lambda y_hat, y: binary_dice_loss(torch.sigmoid(y_hat), y)
        else:
            self.loss_f = lambda y_hat, y: F.binary_cross_entropy_with_logits(y_hat, y.float())
        if optimizer is None:
            optimizer = {'type': 'Adam'}
        self.optimizer = optimizer

        self.testset_names = None

    def compute_y_yhat(self, batch):
        x = batch['x']
        y = (batch['y'] != 0).int()
        y_hat = self.model(x, **{k: v for k, v in batch.items() if k not in ('x', 'y', 'mask')}).squeeze(1)
        y = clip_pad_center(y, y_hat.shape)

        return y, y_hat

    def training_step(self, batch, batch_idx):
        y, y_hat = self.compute_y_yhat(batch)

        if 'mask' in batch:
            mask = clip_pad_center(batch['mask'], y_hat.shape) != 0
            y_hat = y_hat[mask].flatten()
            y = y[mask].flatten()

        loss = self.loss_f(y_hat, y)
        self.log('train-loss', loss.detach().cpu().item())
        return loss

    def _validate(self, batch):
        y, y_hat = self.compute_y_yhat(batch)
        y_sig = torch.sigmoid(y_hat)
        y_pred = y_sig > .5

        if 'mask' in batch:
            mask = clip_pad_center(batch['mask'], y_hat.shape)
            y_hat = y_hat[mask != 0]
            y_sig = y_sig[mask != 0]
            y = y[mask != 0]

        y = y.flatten()
        y_hat = y_hat.flatten()
        y_sig = y_sig.flatten()

        return {
            'loss': self.loss_f(y_hat, y),
            'y_pred': y_pred,
            'y_hat': y_hat,
            'y': y,
            'y_sig': y_sig,
            'metrics': BinaryClassifierNet.metrics(y_sig, y)
        }

    @staticmethod
    def metrics(y_sig, y):
        y_pred = y_sig > 0.5
        return {
            'acc': metricsF.accuracy(y_pred, y),
            'roc': metricsF.auroc(y_sig, y),
            'iou': metricsF.iou(y_pred, y),
        }

    def log_metrics(self, metrics, prefix=''):
        if prefix and not prefix.endswith('-'):
            prefix += '-'
        for k, v in metrics.items():
            # print(prefix+k, v.cpu().item())
            self.log(prefix + k, v.cpu().item())

    def validation_step(self, batch, batch_idx):
        result = self._validate(batch)
        metrics = result['metrics']
        # metrics['acc'] = self.val_accuracy(result['y_sig'] > 0.5, result['y'])
        self.log_metrics(metrics, 'val')
        return result['y_pred']

    def test_step(self, batch, batch_idx, dataloader_idx=0):
        result = self._validate(batch)
        metrics = result['metrics']
        prefix = 'test'
        if self.testset_names:
            prefix = self.testset_names[dataloader_idx]
        self.log_metrics(metrics, prefix)
        return result['y_pred']

    def configure_optimizers(self):
        opt = self.optimizer
        if opt['type'].lower() in ('adam', 'adamax', 'adamw'):
            Adam = {'adam': torch.optim.Adam,
                    'adamax': torch.optim.Adamax,
                    'adamw': torch.optim.AdamW}[opt['type'].lower()]
            kwargs = {k: v for k, v in opt.items() if k in ('weight_decay', 'amsgrad', 'eps')}
            optimizer = Adam(self.parameters(), lr=self.lr, betas=(opt.get('beta', .9), opt.get('beta_sqr', .999)),
                             **kwargs)
        elif opt['type'].lower() == 'asgd':
            kwargs = {k: v for k, v in opt.items() if k in ('lambd', 'alpha', 't0', 'weight_decay')}
            optimizer = torch.optim.ASGD(self.parameters(), lr=self.lr, **kwargs)
        elif opt['type'].lower() == 'sgd':
            kwargs = {k: v for k, v in opt.items() if k in ('momentum', 'dampening', 'nesterov', 'weight_decay')}
            optimizer = torch.optim.SGD(self.parameters(), lr=self.lr, **kwargs)
        else:
            optimizer = torch.optim.Adam(self.parameters(), lr=self.lr)
        return optimizer

    def forward(self, *args, **kwargs):
        return torch.sigmoid(self.model(*args, **kwargs))

    def test(self, datasets):
        if isinstance(datasets, dict):
            self.testset_names, datasets = list(zip(*datasets.items()))
        trainer = pl.Trainer(gpus=[0])
        return trainer.test(self, test_dataloaders=datasets)

    @property
    def p_dropout(self):
        return self.model.p_dropout

    @p_dropout.setter
    def p_dropout(self, p):
        self.model.p_dropout = p
