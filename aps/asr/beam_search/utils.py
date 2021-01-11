#!/usr/bin/env python

# Copyright 2020 Jian Wu
# License: Apache 2.0 (http://www.apache.org/licenses/LICENSE-2.0)

import torch as th

from dataclasses import dataclass
from typing import List, Dict, Union, Tuple, Optional, NoReturn
from aps.const import NEG_INF
from aps.utils import get_logger

logger = get_logger(__name__)
verbose = False


@dataclass
class BeamSearchParam(object):
    """
    Parameters used in beam search
    """
    beam_size: int = 8
    batch_size: Optional[int] = None
    sos: int = 1
    eos: int = 2
    min_len: int = 1
    lm_weight: float = 0
    eos_threshold: float = 0
    device: Union[th.device, str] = "cpu"
    penalty: float = 0
    cov_weight: float = 0
    cov_threshold: float = 0.5
    len_norm: bool = True


class BaseBeamTracker(object):
    """
    Base class (to be inheried)
    """

    def __init__(self, param: BeamSearchParam) -> None:
        self.param = param
        self.align = None  # B x T x U
        self.trans = None  # B x U

    def gather(self, prev_: Optional[th.Tensor], point: Optional[th.Tensor],
               next_: th.Tensor) -> th.Tensor:
        """
        Concat the alignment or transcription step by step
        Args:
            prev_ (Tensor): N x ... x U
            next_ (Tensor): N x ...
            point (Tensor): traceback point
        """
        if prev_ is None:
            return next_[..., None]
        elif point is None:
            return th.cat([prev_, next_[..., None]], -1)
        else:
            return th.cat([prev_[point], next_[..., None]], -1)

    def coverage(self, att_ali: Optional[th.Tensor]) -> Union[th.Tensor, float]:
        """
        Compute coverage score
        Args:
            att_ali (Tensor): N x T, alignment score (weight)
        Return
            cov_score: coverage score
        """
        if att_ali is None or self.param.cov_weight <= 0:
            cov_score = 0
        else:
            assert att_ali is not None
            # N x T x U
            att_mat = self.gather(self.align, None, att_ali)
            # N
            cov = th.sum(th.sum(att_mat, -1) > self.param.cov_threshold,
                         -1,
                         keepdim=True)
            cov_score = self.param.cov_weight * cov
        return cov_score

    def beam_select(self, am_prob: th.Tensor,
                    lm_prob: Union[th.Tensor, float]) -> Tuple[th.Tensor]:
        """
        Perform beam selection
        Args:
            am_prob (Tensor): N x V, acoustic prob
            lm_prob (Tensor): N x V, language prob
        Return:
            topk_score (Tensor): N x K, topk score
            topk_token (Tensor): N x K, topk token ID
        """
        # local pruning: N*beam x beam
        topk_score, topk_token = th.topk(am_prob +
                                         self.param.lm_weight * lm_prob,
                                         self.param.beam_size,
                                         dim=-1)
        return (topk_score, topk_token)

    def trace_hypos(self,
                    point: th.Tensor,
                    score: List[float],
                    trans: th.Tensor,
                    align: Optional[th.Tensor],
                    point_list: List[th.Tensor],
                    token_list: List[th.Tensor],
                    final: bool = False) -> List[Dict]:
        """
        Traceback decoding hypothesis
        Args:
            point (Tensor): starting traceback point
            score (list[float]): final decoding score
            trans (Tensor): traced transcriptions (another way)
            align (Tensor): traced alignments
            point_list (list[Tensor]): traceback point
            token_list (list[Tensor]): token sequence
            final (bool): is final step or not
        """
        align = align[point].cpu()
        final_trans = []
        check_trans = trans[point].tolist()
        for ptr, tok in zip(point_list[::-1], token_list[::-1]):
            final_trans.append(tok[point].tolist())
            point = ptr[point]
        hypos = []
        final_trans = final_trans[::-1]
        for i, s in enumerate(score):
            token = [t[i] for t in final_trans]
            # double check impl of beam search
            assert token[1:] == check_trans[i]
            token_len = len(token) if final else len(token) - 1
            score = s + token_len * self.param.penalty
            hypos.append({
                "score": score / (token_len if self.param.len_norm else 1),
                "trans": token + [self.param.eos] if final else token,
                "align": None if align is None else align[i]
            })
        return hypos


class BeamTracker(BaseBeamTracker):
    """
    A data structure used in beam search algothrim
    """

    def __init__(self, param: BeamSearchParam) -> None:
        super(BeamTracker, self).__init__(param)
        self.token = [
            th.tensor([param.sos] * param.beam_size, device=param.device)
        ]
        self.point = [th.tensor(range(param.beam_size), device=param.device)]
        self.score = th.zeros(param.beam_size, device=param.device)
        self.acmu_score = th.zeros_like(self.score)

    def __getitem__(self, t: int) -> Tuple[th.Tensor, th.Tensor]:
        """
        Return the token and backward point
        """
        return (self.token[t], self.point[t])

    def trace_back(self, final: bool = False) -> Optional[List[Dict]]:
        """
        Return decoding hypothesis
        Args:
            final (bool): is final step or not
        """
        end_eos = (self.token[-1] == self.param.eos).tolist()
        hyp = None
        if not final and sum(end_eos):
            idx = [i for i, end_with_eos in enumerate(end_eos) if end_with_eos]
            idx = th.tensor(idx, device=self.param.device)
            hyp = self._trace_back_hypos(idx, final=False)
        not_end = [not f for f in end_eos]
        if final and sum(not_end):
            idx = th.tensor([i for i, go_on in enumerate(not_end) if go_on],
                            device=self.param.device)
            hyp = self._trace_back_hypos(idx, final=True)
        # filter short utterances
        if hyp:
            hyp = [h for h in hyp if len(h["trans"]) >= self.param.min_len + 2]
            if verbose:
                for h in hyp:
                    logger.info("--- get decoding sequence " +
                                f"{h['trans']}, score = {h['score']:.2f}")
        return hyp

    def prune_beam(self,
                   am_prob: th.Tensor,
                   lm_prob: Union[th.Tensor, float],
                   att_ali: Optional[th.Tensor] = None) -> NoReturn:
        """
        Prune and update score & token & backward point
        Args:
            am_prob (Tensor): N x V, acoustic prob
            lm_prob (Tensor): N x V, language prob
            att_ali (Tensor): N x T, alignment score (weight)
        """
        # local pruning: beam x V => beam x beam
        topk_score, topk_token = self.beam_select(am_prob, lm_prob)

        if len(self.point) == 1:
            self.score += topk_score[0]
            self.acmu_score += topk_score[0]
            self.token.append(topk_token[0])
            self.point.append(self.point[-1])
        else:
            # beam x beam
            acmu_score = self.acmu_score[..., None] + topk_score
            score = acmu_score + self.coverage(att_ali)
            # beam*beam => beam
            self.score, topk_index = th.topk(score.view(-1),
                                             self.param.beam_size,
                                             dim=-1)
            self.token.append(topk_token.view(-1)[topk_index])
            # point to father's node
            self.point.append(topk_index // self.param.beam_size)
            # update accumulated score (AM + LM)
            self.acmu_score = acmu_score.view(-1)[topk_index]
        # gather stats
        self.trans = self.gather(self.trans, self.point[-1], self.token[-1])
        if att_ali is not None:
            self.align = self.gather(self.align, self.point[-1], att_ali)

    def _trace_back_hypos(self,
                          point: th.Tensor,
                          final: bool = False) -> List[Dict]:
        """
        Trace back the decoding transcription sequence from the current time point
        Args:
            point (Tensor): initial backward point
        """
        score = self.score[point].tolist()
        self.acmu_score[point] = NEG_INF
        return self.trace_hypos(point,
                                score,
                                self.trans,
                                self.align,
                                self.point,
                                self.token,
                                final=final)


class BatchBeamTracker(BaseBeamTracker):
    """
    A data structure used in batch version of the beam search
    """

    def __init__(self, batch_size: int, param: BeamSearchParam) -> None:
        super(BatchBeamTracker, self).__init__(param)
        self.param = param
        self.batch_size = batch_size
        self.token = [
            th.tensor([[param.sos] * param.beam_size] * batch_size,
                      device=param.device)
        ]
        self.point = [
            th.tensor([list(range(param.beam_size))] * batch_size,
                      device=param.device)
        ]
        self.score = th.zeros(batch_size, param.beam_size, device=param.device)
        self.acmu_score = th.zeros_like(self.score)
        self.step_point = th.arange(0,
                                    param.beam_size * batch_size,
                                    param.beam_size,
                                    device=param.device)

    def __getitem__(self, t: int) -> Tuple[th.Tensor, th.Tensor]:
        """
        Return the token and backward point
        """
        point = self.point[t] + self.step_point[:, None]
        token = self.token[t]
        return (token.view(-1), point.view(-1))

    def trace_back(self, batch, final: bool = False) -> Optional[List[Dict]]:
        """
        Return end flags
        """
        end_eos = (self.token[-1][batch] == self.param.eos).tolist()
        hyp = None
        if not final and sum(end_eos):
            idx = [i for i, end_with_eos in enumerate(end_eos) if end_with_eos]
            idx = th.tensor(idx, device=self.score.device)
            hyp = self._trace_back_hypos(batch, idx, final=False)
        not_end = [not f for f in end_eos]
        if final and sum(not_end):
            idx = th.tensor([i for i, go_on in enumerate(not_end) if go_on],
                            device=self.score.device)
            hyp = self._trace_back_hypos(batch, idx, final=True)
        # filter short utterances
        if hyp:
            hyp = [h for h in hyp if len(h["trans"]) >= self.param.min_len + 2]
        return hyp

    def prune_beam(self,
                   am_prob: th.Tensor,
                   lm_prob: Union[th.Tensor, float],
                   att_ali: Optional[th.Tensor] = None) -> NoReturn:
        """
        Prune and update score & token & backward point
        Args:
            am_prob (Tensor): N x V, acoustic prob
            lm_prob (Tensor): N x V, language prob
            att_ali (Tensor): N x T, alignment score (weight)
        """
        # local pruning: beam x V => beam x beam
        topk_score, topk_token = self.beam_select(am_prob, lm_prob)
        if len(self.point) == 1:
            # N x beam
            self.score += topk_score[::self.param.beam_size]
            self.acmu_score += topk_score[::self.param.beam_size]
            self.point.append(self.point[-1])
            token = topk_token[::self.param.beam_size]
        else:
            # N*beam x beam = N*beam x 1 + N*beam x beam
            acmu_score = self.acmu_score.view(-1, 1) + topk_score
            score = acmu_score + self.coverage(att_ali)
            # N x beam*beam => N x beam
            self.score, topk_index = th.topk(score.view(self.batch_size, -1),
                                             self.param.beam_size,
                                             dim=-1)
            # update accmulated score (AM + LM)
            self.acmu_score = th.gather(acmu_score.view(self.batch_size, -1),
                                        -1, topk_index)
            # N x beam, point to father's node
            self.point.append(topk_index // self.param.beam_size)
            # N x beam*beam => N x beam
            token = th.gather(topk_token.view(self.batch_size, -1), -1,
                              topk_index)
        self.token.append(token.clone())
        # gather stats
        token, point = self[-1]
        self.trans = self.gather(self.trans, point, token)
        if att_ali is not None:
            self.align = self.gather(self.align, point, att_ali)

    def _trace_back_hypos(self,
                          batch: int,
                          point: th.Tensor,
                          final: bool = False) -> List[Dict]:
        """
        Trace back the decoding transcription sequence from the current time point
        Args:
            batch (int): batch index
            point (Tensor): initial backward point
        """
        score = self.score[batch, point].tolist()
        self.acmu_score[batch, point] = NEG_INF
        trans = th.chunk(self.trans, self.batch_size, 0)[batch]
        align = th.chunk(self.align, self.batch_size, 0)[batch]
        points = [p[batch] for p in self.point]
        tokens = [t[batch] for t in self.token]
        return self.trace_hypos(point,
                                score,
                                trans,
                                align,
                                points,
                                tokens,
                                final=final)
