#!/usr/bin/python
# -*- coding: utf-8 -*-
# 
# Developed by Haozhe Xie <cshzxie@gmail.com>

import matplotlib.pyplot as plt
import numpy as np
import os
import torch
import torch.backends.cudnn
import torch.utils.data
import torchvision.transforms

import utils.binvox_visualization
import utils.data_loaders
import utils.data_transforms
import utils.network_utils

from datetime import datetime as dt
from tensorboardX import SummaryWriter
from time import time

from models.discriminator import Discriminator
from models.generator import Generator
from models.image_encoder import ImageEncoder

def test_net(cfg, epoch_idx=-1, output_dir=None, test_data_loader=None, test_writer=None, generator=None, image_encoder=None):
    # Enable the inbuilt cudnn auto-tuner to find the best algorithm to use
    torch.backends.cudnn.benchmark  = True

    # Set up data loader
    if test_data_loader is None:
        # Set up data augmentation
        IMG_SIZE  = cfg.CONST.IMG_H, cfg.CONST.IMG_W
        CROP_SIZE = cfg.TRAIN.CROP_IMG_H, cfg.TRAIN.CROP_IMG_W
        test_transforms  = utils.data_transforms.Compose([
            utils.data_transforms.Normalize(mean=cfg.DATASET.MEAN, std=cfg.DATASET.STD),
            utils.data_transforms.CenterCrop(IMG_SIZE, CROP_SIZE),
            utils.data_transforms.RandomBackground(cfg.TEST.RANDOM_BG_COLOR_RANGE),
            utils.data_transforms.ToTensor(),
        ])

        dataset_loader   = utils.data_loaders.DATASET_LOADER_MAPPING[cfg.DATASET.DATASET_NAME](cfg)
        n_views          = np.random.randint(cfg.CONST.N_VIEWS) + 1 if cfg.TRAIN.RANDOM_NUM_VIEWS else cfg.CONST.N_VIEWS
        test_data_loader = torch.utils.data.DataLoader(
            dataset=dataset_loader.get_dataset(cfg.TEST.DATASET_PORTION, n_views, test_transforms),
            batch_size=1,
            num_workers=1, pin_memory=True, shuffle=False)

    # Summary writer for TensorBoard
    need_to_close_writer = False
    if output_dir is None:
        need_to_close_writer = True
        output_dir  = os.path.join(cfg.DIR.OUT_PATH, '%s', dt.now().isoformat())
        log_dir     = output_dir % 'logs'
        test_writer = SummaryWriter(os.path.join(log_dir, 'test'))
    
    # Set up networks
    if generator is None or image_encoder is None:
        generator            = Generator(cfg)
        image_encoder        = ImageEncoder(cfg)

        if torch.cuda.is_available():
            generator.cuda()
            image_encoder.cuda()

        # TODO: load weights from file

    # Set up loss functions
    bce_loss = torch.nn.BCELoss()

    # Testing loop
    n_samples = len(test_data_loader)
    test_iou  = dict()
    test_image_encoder_loss = []
    for sample_idx, (taxonomy_name, sample_name, rendering_images, voxel) in enumerate(test_data_loader):
        taxonomy_name = taxonomy_name[0]
        sample_name   = sample_name[0]

        # Switch models to training mode
        generator.eval();
        image_encoder.eval();

        with torch.no_grad():
            # Get data from data loader
            rendering_images = utils.network_utils.var_or_cuda(rendering_images)
            voxel            = utils.network_utils.var_or_cuda(voxel)

            # Test the generator
            rendering_image_features    = image_encoder(rendering_images)
            generated_voxel             = generator(rendering_image_features)

            # Loss
            image_encoder_loss          = bce_loss(generated_voxel, voxel) * 10
            test_image_encoder_loss.append(image_encoder_loss)

            # IoU per sample
            sample_iou = []
            for th in cfg.TEST.VOXEL_THRESH:
                _voxel       = torch.ge(generated_voxel, th).float()
                intersection = torch.sum(_voxel.mul(voxel)).float()
                union        = torch.sum(torch.ge(_voxel.add(voxel), 1)).float()
                sample_iou.append((intersection / union).item())

            # IoU per taxonomy
            if not taxonomy_name in test_iou:
                test_iou[taxonomy_name] = {
                    'n_samples': 0,
                    'iou': []
                }
            test_iou[taxonomy_name]['n_samples'] += 1
            test_iou[taxonomy_name]['iou'].append(sample_iou)

            # print
            print('[INFO] %s Test[%d/%d] Taxonomy = %s Sample = %s ILoss = %.4f IoU = %s' % \
                (dt.now(), sample_idx + 1, n_samples, taxonomy_name, sample_name, image_encoder_loss, sample_iou))

    # Output testing results
    mean_iou = []
    for taxonomy_name in test_iou:
        test_iou[taxonomy_name]['iou'] = np.mean(test_iou[taxonomy_name]['iou'], axis=0)
        mean_iou.append(test_iou[taxonomy_name]['iou'] * test_iou[taxonomy_name]['n_samples'])
    mean_iou = np.mean(mean_iou, axis=0) / n_samples

    # Print header
    print('====== TEST RESULTS ======')
    print('Taxonomy', end='\t')
    for th in cfg.TEST.VOXEL_THRESH:
        print('t=%.2f' % th, end='\t')
    print()
    # Print body
    for taxonomy_name in test_iou:
        print(taxonomy_name, end='\t')
        for ti in test_iou[taxonomy_name]['iou']:
            print('%.4f' % ti, end='\t')
        print()
    # Print mean IoU for each threshold
    print('Overall ', end='\t')
    for mi in mean_iou:
        print('%.4f' % mi, end='\t')
    print('\n')

    # Add testing results to TensorBoard
    max_iou = np.max(mean_iou)
    if not epoch_idx == -1:
        test_writer.add_scalar('Generator/MeanLoss', image_encoder_loss, epoch_idx)
        test_writer.add_scalar('Generator/IoU', max_iou, epoch_idx)

    # Close SummaryWriter for TensorBoard
    if need_to_close_writer:
        test_writer.close()

    return max_iou
