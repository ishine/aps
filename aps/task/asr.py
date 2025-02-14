#!/usr/bin/env python

# Copyright 2020 Jian Wu
# License: Apache 2.0 (http://www.apache.org/licenses/LICENSE-2.0)
"""
For ASR task
"""
import warnings
import torch as th
import torch.nn as nn

import torch.nn.functional as tf

# for RNNT loss, we have three options:
# warp-transducer: https://github.com/HawkAaron/warp-transducer
# warp-rnnt: https://github.com/1ytic/warp-rnnt
# torchaudio: https://pytorch.org/audio/stable/functional.html#rnnt-loss
try:
    from warp_rnnt import rnnt_loss as warp_rnnt_v1
except ImportError:
    warp_rnnt_v1 = None
try:
    from warprnnt_pytorch import rnnt_loss as warp_rnnt_v2
except ImportError:
    warp_rnnt_v2 = None
try:
    from torchaudio.functional import rnnt_loss as torchaudio_rnnt
except ImportError:
    torchaudio_rnnt = None

from typing import Tuple, Dict, NoReturn, Optional
from aps.task.base import Task
from aps.task.objf import ce_objf, ls_objf, ctc_objf
from aps.const import IGNORE_ID
from aps.libs import ApsRegisters

__all__ = ["CtcTask", "CtcXentHybridTask", "TransducerTask", "LmXentTask"]


def compute_accu(dec_out: th.Tensor, tgt_pad: th.Tensor) -> Tuple[float]:
    """
    Compute frame-level accuracy
    Args:
        dec_out: N x T, decoder output
        tgt_ref: N x T, padding target labels
    """
    # N x (To+1)
    pred = th.argmax(dec_out.detach(), dim=-1)
    # ignore mask, -1
    mask = (tgt_pad != IGNORE_ID)
    # numerator
    num_correct = th.sum(pred[mask] == tgt_pad[mask]).float()
    # denumerator
    total = th.sum(mask)
    # return pair
    accu = num_correct / total
    return (accu.item(), total.item())


def prep_asr_label(
        tgt_ori: th.Tensor,
        tgt_len: th.Tensor,
        pad_value: int,
        sos_value: int = -1,
        eos_value: int = -1) -> Tuple[th.Tensor, Optional[th.Tensor]]:
    """
    Process asr label for loss and accu computation
    Args:
        tgt_ori: padded target labels
        tgt_len: target length
        pad_value: padding value, e.g., ignore_id
        sos_value: value to pad in sos position
        eos_value: value to pad in eos position
    """
    # N x To, -1 => EOS
    if pad_value != IGNORE_ID:
        tgt_infer = tgt_ori.masked_fill(tgt_ori == IGNORE_ID, pad_value)
    else:
        tgt_infer = tgt_ori
    # add sos if needed
    if sos_value >= 0:
        # N x (To+1), pad sos
        tgt_infer = tf.pad(tgt_infer, (1, 0), value=sos_value)
    if eos_value >= 0:
        # N x (To+1), pad IGNORE_ID
        tgt_refer = tf.pad(tgt_ori, (0, 1), value=IGNORE_ID)
        # replace with eos
        tgt_refer = tgt_refer.scatter(1, tgt_len[:, None], eos_value)
    else:
        tgt_refer = None
    return tgt_infer, tgt_refer


def load_label_count(label_count: str) -> Optional[th.Tensor]:
    """
    Load tensor from a label count file
    Args:
        label_count: path of the label count file
    """
    if not label_count:
        return None
    counts = []
    with open(label_count, "r") as lc_fd:
        for raw_line in lc_fd:
            toks = raw_line.strip().split()
            num_toks = len(toks)
            if num_toks not in [1, 2]:
                raise RuntimeError(
                    f"Detect format error in label count file: {raw_line}")
            counts.append(float(toks[0] if num_toks == 1 else toks[1]))
    counts = th.tensor(counts)
    num_zeros = th.sum(counts == 0).item()
    if num_zeros:
        warnings.warn(f"Got {num_zeros} labels for zero counting")
    return th.clamp_min(counts, 1)


class ASRTask(Task):
    """
    Base class for ASR tasks
    """

    def __init__(self,
                 nnet: nn.Module,
                 reduction: str = "batchmean",
                 description: str = ""):
        super(ASRTask, self).__init__(nnet, description=description)
        if reduction not in ["mean", "batchmean"]:
            raise ValueError(f"Unsupported reduction option: {reduction}")
        self.reduction = reduction


@ApsRegisters.task.register("asr@ctc")
class CtcTask(ASRTask):
    """
    For CTC objective function only
    Args:
        nnet: AM network
        blank: blank id for CTC
        reduction: reduction option applied to the sum of the loss
    """

    def __init__(self,
                 nnet: nn.Module,
                 blank: int = 0,
                 reduction: str = "batchmean") -> None:
        super(CtcTask, self).__init__(
            nnet,
            reduction=reduction,
            description="CTC objective function training for ASR")
        self.ctc_blank = blank

    def forward(self, egs: Dict) -> Dict:
        """
        Compute CTC loss, egs contains:
        src_pad: N x Ti x F, src_len: N, tgt_pad: N x To, tgt_len: N
        """
        # ctc_enc: N x T x V
        _, ctc_enc, enc_len = self.nnet(egs["src_pad"], egs["src_len"])
        ctc_loss = ctc_objf(ctc_enc,
                            egs["tgt_pad"],
                            enc_len,
                            egs["tgt_len"],
                            blank=self.ctc_blank,
                            reduction=self.reduction,
                            add_softmax=True)
        # ignore length of eos
        assert th.sum(egs["tgt_len"]) == egs["#tok"] - ctc_enc.shape[0]
        return {"loss": ctc_loss}


@ApsRegisters.task.register("asr@ctc_xent")
class CtcXentHybridTask(ASRTask):
    """
    For encoder/decoder attention based AM training. (CTC for encoder, Xent for decoder)
    Args:
        nnet: AM network
        blank: blank id for CTC
        reduction: reduction option applied to the sum of the loss
        lsm_factor: label smoothing factor
        lsm_method: label smoothing method (uniform|unigram)
        ctc_weight: CTC weight
        label_count: label count file
    """

    def __init__(self,
                 nnet: nn.Module,
                 blank: int = 0,
                 reduction: str = "batchmean",
                 lsm_factor: float = 0,
                 lsm_method: str = "uniform",
                 ctc_weight: float = 0,
                 label_count: str = "") -> None:
        super(CtcXentHybridTask, self).__init__(
            nnet,
            reduction=reduction,
            description="CTC + Xent multi-task training for ASR")
        if lsm_method == "unigram" and not label_count:
            raise RuntimeError(
                "Missing label_count to use unigram label smoothing")
        self.ctc_weight = ctc_weight
        self.lsm_factor = lsm_factor
        self.ctc_kwargs = {
            "blank": blank,
            "reduction": self.reduction,
            "add_softmax": True
        }
        self.lsm_kwargs = {
            "method": lsm_method,
            "reduction": self.reduction,
            "lsm_factor": lsm_factor,
            "label_count": load_label_count(label_count)
        }

    def forward(self, egs: Dict) -> Dict:
        """
        Compute CTC & Attention loss, egs contains:
        src_pad: N x Ti x F, src_len: N, tgt_pad: N x To, tgt_len: N, ssr: float if needed
        """
        # tgt_infer: N x To+1 (pad eos, replace ignore_id with eos, used in decoder)
        # tgt_refer: N x To+1 (pad eos, used in loss computation)
        tgt_infer, tgt_refer = prep_asr_label(egs["tgt_pad"],
                                              egs["tgt_len"],
                                              self.nnet.eos,
                                              sos_value=self.nnet.sos,
                                              eos_value=self.nnet.eos)
        # outs: N x (To+1) x V
        ssr = egs["ssr"] if "ssr" in egs else 0
        outs, ctc_enc, enc_len = self.nnet(egs["src_pad"],
                                           egs["src_len"],
                                           tgt_infer,
                                           egs["tgt_len"] + 1,
                                           ssr=ssr)
        # compute loss
        if self.lsm_factor > 0:
            att_loss = ls_objf(outs, tgt_refer, **self.lsm_kwargs)
        else:
            att_loss = ce_objf(outs, tgt_refer, reduction=self.reduction)

        stats = {}
        if self.ctc_weight > 0:
            ctc_loss = ctc_objf(ctc_enc, egs["tgt_pad"], enc_len,
                                egs["tgt_len"], **self.ctc_kwargs)
            stats["@ctc"] = ctc_loss.item()
            stats["xent"] = att_loss.item()
        else:
            ctc_loss = 0
        loss = self.ctc_weight * ctc_loss + (1 - self.ctc_weight) * att_loss
        # compute accu
        accu, den = compute_accu(outs, tgt_refer)
        # check coding error
        assert den == egs["#tok"]
        # add to reporter
        stats["accu"] = accu
        stats["loss"] = loss
        return stats


@ApsRegisters.task.register("asr@transducer")
class TransducerTask(ASRTask):
    """
    For transducer objective training.
    Args:
        nnet: Transducer based model
        interface: which transducer loss api to use (torchaudio|warp_rnnt|warprnnt_pytorch)
        reduction: reduction option applied to the sum of the loss
        blank: blank ID for transducer loss computation
    """

    def __init__(self,
                 nnet: nn.Module,
                 interface: str = "torchaudio",
                 reduction: str = "batchmean",
                 blank: int = 0) -> None:
        super(TransducerTask, self).__init__(
            nnet,
            reduction=reduction,
            description="Transducer objective for ASR training")
        self.blank = blank
        self._setup_rnnt_backend(interface)

    def _setup_rnnt_backend(self, interface: str) -> NoReturn:
        """
        Setup RNNT loss impl in the backend
        """
        api = {
            "warp_rnnt": warp_rnnt_v1,
            "torchaudio": torchaudio_rnnt,
            "warprnnt_pytorch": warp_rnnt_v2
        }
        if interface not in api:
            raise ValueError(
                f"Unsupported transducer loss interface: {interface}")
        self.rnnt_objf = api[interface]
        if self.rnnt_objf is None:
            raise RuntimeError(f"import {interface} failed ..., " +
                               "please check python envrionments")
        self.warp_rnnt_v1 = interface == "warp_rnnt"

    def forward(self, egs: Dict) -> Dict:
        """
        Compute transducer loss, egs contains:
        src_pad: N x Ti x F, src_len: N, tgt_pad: N x To, tgt_len: N
        """
        # tgt_infer: N x To+1 (start with blank, replace ignore_id with blank)
        tgt_infer = prep_asr_label(egs["tgt_pad"],
                                   egs["tgt_len"],
                                   self.blank,
                                   sos_value=self.blank,
                                   eos_value=self.blank)[0]
        # N x Ti x To+1 x V
        _, dec_out, enc_len = self.nnet(egs["src_pad"], egs["src_len"],
                                        tgt_infer, egs["tgt_len"] + 1)
        # add log_softmax if use https://github.com/1ytic/warp-rnnt
        if self.warp_rnnt_v1:
            dec_out = tf.log_softmax(dec_out, -1)
        # compute loss
        loss = self.rnnt_objf(dec_out,
                              egs["tgt_pad"].to(th.int32),
                              enc_len.to(th.int32),
                              egs["tgt_len"].to(th.int32),
                              blank=self.blank,
                              reduction="sum")
        denorm = th.sum(
            egs["tgt_len"]) if self.reduction == "mean" else dec_out.shape[0]
        return {"loss": loss / denorm}


@ApsRegisters.task.register("asr@lm")
class LmXentTask(ASRTask):
    """
    For LM training (Xent loss)
    Args:
        nnet: language model
        bptt_mode: reuse hidden state in previous batch (for BPTT)
        reduction: reduction option applied to the sum of the loss
    """

    def __init__(self,
                 nnet: nn.Module,
                 bptt_mode: bool = False,
                 reduction: str = "batchmean") -> None:
        super(LmXentTask, self).__init__(nnet,
                                         reduction=reduction,
                                         description="Xent for LM training")
        self.hidden = None
        self.bptt_mode = bptt_mode

    def forward(self, egs: Dict) -> Dict:
        """
        Compute CE loss, egs contains src: N x T+1, tgt: N x T+1, len: N
        """
        # pred: N x T+1 x V
        if self.bptt_mode:
            if "reset" in egs and egs["reset"]:
                self.hidden = None
            pred, self.hidden = self.nnet(egs["src"], self.hidden)
        else:
            pred, _ = self.nnet(egs["src"], None, egs["len"])
        loss = ce_objf(pred, egs["tgt"], reduction=self.reduction)
        accu, den = compute_accu(pred, egs["tgt"])
        # check coding error
        assert den == egs["#tok"]
        # ppl is derived from xent, so we pass loss to it
        ppl = loss if self.reduction == "mean" else loss * pred.shape[0] / den
        stats = {"accu": accu, "loss": loss, "@ppl": ppl.item()}
        return stats
