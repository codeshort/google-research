# coding=utf-8
# Copyright 2020 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# python3
"""MLP VAE on images task family."""

import numpy as np
import sonnet as snt

from task_set import registry
from task_set.tasks import base
from task_set.tasks import generative_utils
from task_set.tasks import utils
import tensorflow.compat.v1 as tf


@registry.task_registry.register_sampler("mlp_vae_family")
def sample_mlp_vae_family_cfg(seed):
  """Samples a task config for a MLP VAE model on image datasets.

  These configs are nested python structures that provide enough information
  to create an instance of the problem.

  Args:
    seed: int Random seed to generate task from.

  Returns:
    A nested dictionary containing a configuration.
  """
  rng = np.random.RandomState(seed)
  cfg = {}
  enc_n_layers = rng.choice([1, 2, 3, 4])
  cfg["enc_hidden_units"] = [
      utils.sample_log_int(rng, 32, 128) for _ in range(enc_n_layers)
  ]

  dec_n_layers = rng.choice([1, 2, 3])
  cfg["dec_hidden_units"] = [
      utils.sample_log_int(rng, 32, 128) for _ in range(dec_n_layers)
  ]

  cfg["activation"] = utils.sample_activation(rng)
  cfg["w_init"] = utils.sample_initializer(rng)
  cfg["dataset"] = utils.sample_image_dataset(rng)
  return cfg


@registry.task_registry.register_getter("mlp_vae_family")
def get_mlp_vae_family(cfg):
  """Gets a task for the given cfg.

  Args:
    cfg: config specifying the model generated by `sample_mlp_vae_family_cfg`.

  Returns:
    base.BaseTask for the given config.
  """
  act_fn = utils.get_activation(cfg["activation"])
  w_init = utils.get_initializer(cfg["w_init"])
  init = {"w": w_init}

  datasets = utils.get_image_dataset(cfg["dataset"])

  def _build(batch):
    """Build the sonnet module."""
    flat_img = snt.BatchFlatten()(batch["image"])
    latent_size = cfg["enc_hidden_units"][-1]

    def encoder_fn(net):
      hidden_units = cfg["enc_hidden_units"][:-1] + [latent_size * 2]
      mod = snt.nets.MLP(hidden_units, activation=act_fn, initializers=init)
      outputs = mod(net)
      return generative_utils.LogStddevNormal(outputs)

    encoder = snt.Module(encoder_fn, name="encoder")

    def decoder_fn(net):
      hidden_units = cfg["dec_hidden_units"] + [flat_img.shape.as_list()[1] * 2]
      mod = snt.nets.MLP(hidden_units, activation=act_fn, initializers=init)
      net = mod(net)
      net = tf.clip_by_value(net, -10, 10)
      return generative_utils.QuantizedNormal(mu_log_sigma=net)

    decoder = snt.Module(decoder_fn, name="decoder")
    zshape = tf.stack([tf.shape(flat_img)[0], 2 * latent_size])
    prior = generative_utils.LogStddevNormal(tf.zeros(shape=zshape))

    log_p_x, kl_term = generative_utils.log_prob_elbo_components(
        encoder, decoder, prior, flat_img)
    elbo = log_p_x - kl_term

    metrics = {
        "kl_term": tf.reduce_mean(kl_term),
        "log_kl_term": tf.log(tf.reduce_mean(kl_term)),
        "log_p_x": tf.reduce_mean(log_p_x),
        "elbo": tf.reduce_mean(elbo),
        "log_neg_log_p_x": tf.log(-tf.reduce_mean(elbo))
    }

    return base.LossAndAux(-tf.reduce_mean(elbo), metrics)

  return base.DatasetModelTask(lambda: snt.Module(_build), datasets)