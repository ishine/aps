#!/usr/bin/env python

# Copyright 2019 Jian Wu
# License: Apache 2.0 (http://www.apache.org/licenses/LICENSE-2.0)

import sys
import time
import random
import logging

import torch as th
import numpy as np

from typing import NoReturn, Tuple, Any, Union, Optional

# google-style log prefix
common_logger_format = "{levelname} {asctime} PID:{process} {module}:{lineno}] {message}"
stdout_logger_format = "{levelname} {asctime} PID:{process}] {message}"
time_format = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str,
               date_format: str = time_format,
               file: bool = False) -> logging.Logger:
    """
    Get logger instance
    Args:
        name: logger name
        format_str|date_format: to configure logging format
        file: if true, treat name as the name of the logging file
    """

    def get_handler(handler, format_str):
        formatter = logging.Formatter(fmt=format_str,
                                      datefmt=date_format,
                                      style="{")
        handler.setFormatter(formatter)
        return handler

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    stdout_handler = get_handler(logging.StreamHandler(sys.stdout),
                                 stdout_logger_format)
    logger.addHandler(stdout_handler)
    if file:
        output_handler = get_handler(logging.FileHandler(name),
                                     common_logger_format)
        logger.addHandler(output_handler)
    return logger


def load_obj(obj: Any, device: Union[th.device, str]) -> Any:
    """
    Offload tensor object in obj to cuda device
    Args:
        obj: Arbitrary object
        device: target device ("cpu", "cuda" or th.device object)
    """

    def cuda(obj):
        return obj.to(device) if isinstance(obj, th.Tensor) else obj

    if isinstance(obj, dict):
        return {key: load_obj(obj[key], device) for key in obj}
    elif isinstance(obj, list):
        return [load_obj(val, device) for val in obj]
    else:
        return cuda(obj)


def get_device_ids(device_ids: Union[str, int]) -> Tuple[int]:
    """
    Got device ids
    Args:
        device_ids: int or string like "0,1"
    """
    if not th.cuda.is_available():
        raise RuntimeError("CUDA device unavailable... exist")
    # None or 0
    if not device_ids:
        # detect number of device available
        dev_cnt = th.cuda.device_count()
        device_ids = tuple(range(0, dev_cnt))
    elif isinstance(device_ids, int):
        device_ids = (device_ids,)
    elif isinstance(device_ids, str):
        device_ids = tuple(map(int, device_ids.split(",")))
    else:
        raise ValueError(f"Unsupported value for device_ids: {device_ids}")
    return device_ids


def set_seed(seed_str: str) -> Optional[int]:
    """
    Set random seed for numpy & torch & cuda
    Args:
        seed_str: string
    """
    # set random seed
    if not seed_str or seed_str == "none":
        return None
    else:
        seed = int(seed_str)
        random.seed(seed)
        np.random.seed(seed)
        th.random.manual_seed(seed)
        th.cuda.manual_seed_all(seed)
        return seed


class SimpleTimer(object):
    """
    A simple timer
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> NoReturn:
        self.start = time.time()

    def elapsed(self) -> float:
        return (time.time() - self.start) / 60
