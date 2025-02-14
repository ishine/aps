#!/usr/bin/env python

# Copyright 2019 Jian Wu
# License: Apache 2.0 (http://www.apache.org/licenses/LICENSE-2.0)
"""
Beam search for transducer based AM
"""

import torch as th
import torch.nn as nn
import torch.nn.functional as tf

from copy import deepcopy
from typing import List, Dict, Tuple, Optional
from aps.utils import get_logger
from aps.const import MIN_F32
from aps.asr.beam_search.lm import lm_score_impl, LmType

logger = get_logger(__name__)


class Node(object):
    """
    Beam node for RNNT beam search
    """

    def __init__(self, score: th.Tensor, stats: Dict) -> None:
        self.score = score
        self.stats = stats

    def __getitem__(self, key: str):
        return self.stats[key]

    def clone(self):
        lm_state = self.stats["lm_state"]
        # trick to avoid ngram state copy
        self.stats["lm_state"] = None
        copy = deepcopy(self)
        copy.stats["lm_state"] = lm_state
        self.stats["lm_state"] = lm_state
        return copy


def is_valid_decoder(decoder: nn.Module, blank: int):
    """
    Whether decoder is valid
    """
    if not hasattr(decoder, "pred"):
        raise RuntimeError("Function pred should defined in decoder network")
    if not hasattr(decoder, "joint"):
        raise RuntimeError("Function joint should defined in decoder network")
    if blank != decoder.vocab_size - 1:
        raise RuntimeError("Hard code for blank = decoder.vocab_size - 1 here")


class TransducerBeamSearch(nn.Module):
    """
    Transducer prefix beam search in Sequence Transduction with
    Recurrent Neural Networks: Algorithm 1
    """

    def __init__(self,
                 decoder: nn.Module,
                 lm: Optional[LmType] = None,
                 blank: int = -1):
        super(TransducerBeamSearch, self).__init__()
        # check valid
        is_valid_decoder(decoder, blank)
        self.decoder = decoder
        self.lm = lm
        self.blank = blank
        self.device = next(self.decoder.parameters()).device
        logger.info(f"TransducerBeamSearch: use blank = {blank}")

    def _pred_step(
            self,
            prev_tok: int,
            pred_hid: Optional[th.Tensor] = None
    ) -> Tuple[th.Tensor, th.Tensor]:
        """
        Make one prediction step with projection
        """
        prev_tok = th.tensor([[prev_tok]], dtype=th.int64, device=self.device)
        # decoder + dec_proj
        dec_out, dec_hid = self.decoder.pred(prev_tok, hidden=pred_hid)
        return dec_out, dec_hid

    def _lm_score(self, prev_tok, state):
        """
        Predict LM score
        """
        prev_tok = th.tensor([prev_tok], dtype=th.int64, device=self.device)
        score, state = lm_score_impl(self.lm, None, prev_tok, state)
        return score[0], state

    def _joint_log_prob(self, enc_proj: th.Tensor,
                        dec_proj: th.Tensor) -> th.Tensor:
        """
        Get log prob using joint network
        """
        # predict: N x 1 x V => V
        joint = self.decoder.joint(enc_proj, dec_proj)[0, 0]
        # log prob
        return tf.log_softmax(joint, dim=-1)

    def _merge_by_prefix(self, list_a: List[Node],
                         enc_frame: th.Tensor) -> List[Node]:
        """
        Line 5-6 in Algorithm 1
        """
        num_nodes = len(list_a)
        for j in range(num_nodes - 1):
            for i in range(j + 1, num_nodes):
                # node ni, nj
                ni, nj = list_a[i], list_a[j]
                # sequence si, sj
                si, sj = ni["tok_seq"], nj["tok_seq"]
                # sequence length li, lj
                li, lj = len(si), len(sj)
                # si is the prefix of sj
                is_prefix = li < lj and si[:li] == sj[:li]
                if not is_prefix:
                    continue
                dec_out, dec_hid = self._pred_step(si[-1], ni["dec_hid"])
                log_prob = self._joint_log_prob(enc_frame, dec_out)
                score = ni.score + log_prob[sj[li]]
                for k in range(li, lj - 1):
                    log_prob = self._joint_log_prob(enc_frame, nj["dec_out"][k])
                    score += log_prob[sj[k + 1]]
                # update score
                nj.score = th.logaddexp(nj.score, score)
        return list_a

    def greedy_search(self, enc_proj: th.Tensor):
        """
        Greedy search algorithm for Transducer
        Args:
            enc_proj: 1 x T x J
        """
        score = 0
        trans = []
        _, num_frames, _ = enc_proj.shape
        dec_out, dec_hid = self._pred_step(self.blank)
        for t in range(num_frames):
            log_prob = self._joint_log_prob(enc_proj[:, t], dec_out)
            value, index = th.max(log_prob, dim=-1)
            score += value.item()
            index = index.item()
            # not blank
            if index != self.blank:
                dec_out, dec_hid = self._pred_step(index, dec_hid)
                trans += [index]
        return [{"score": score, "trans": [self.blank] + trans + [self.blank]}]

    def forward(self,
                enc_out: th.Tensor,
                beam_size: int = 16,
                nbest: int = 8,
                lm_weight: float = 0,
                len_norm: bool = True) -> List[Dict]:
        """
        Beam search (not prefix beam search) algorithm for Transducer
        Args:
            enc_out (Tensor): (1) x T x D
            beam_size (int): beam size of the beam search
            nbest (int): return nbest hypos
        """
        N, T, D = enc_out.shape
        if N != 1:
            raise RuntimeError(
                f"Got batch size {N:d}, now only support one utterance")
        vocab_size = self.decoder.vocab_size
        if beam_size > vocab_size:
            raise RuntimeError(
                f"Beam size ({beam_size}) > vocabulary size ({vocab_size})")
        logger.info(f"--- shape of the encoder output: {T} x {D}")
        # apply projection at first
        enc_proj = self.decoder.enc_proj(enc_out)
        # greedy search
        if beam_size == 1:
            return self.greedy_search(enc_proj)

        with_lm = self.lm is not None and lm_weight > 0
        init = {
            "tok_seq": [self.blank],
            "dec_hid": None,
            "dec_out": [],
            "lm_state": None
        }
        # list_a, list_b: A, B in Algorithm 1
        list_b = [Node(th.tensor(0.0).to(self.device), init)]
        for t in range(T):
            # 1 x D:
            enc_frame = enc_proj[:, t]
            list_a = self._merge_by_prefix(list_b, enc_frame)
            list_b = []

            # cache
            cache_logp = [th.stack([a.score for a in list_a])]
            cache_dec_hid = []
            cache_dec_out = []
            cache_node = []
            cache_lm = []

            best_idx = 0
            best_tok = cache_logp[best_idx].argmax().item()
            # y^* in Algorithm 1
            best_node = list_a[best_tok]

            while True:
                # predict network: compute Pr(y^*)
                dec_out, dec_hid = self._pred_step(best_node["tok_seq"][-1],
                                                   best_node["dec_hid"])
                # joint network
                log_prob = self._joint_log_prob(enc_frame, dec_out)

                # add terminal node (end with blank)
                # update Pr(y^*) = Pr(y^*)Pr(b|y,t)
                blank_node = best_node.clone()
                blank_node.score += log_prob[self.blank]

                merge_done = False
                for node_b in list_b:
                    if blank_node["tok_seq"] == node_b["tok_seq"]:
                        node_b.score = max(blank_node.score, node_b.score)
                        merge_done = True
                        break
                if not merge_done:
                    list_b.append(blank_node)

                # without blank
                if with_lm:
                    lm_score, lm_state = self._lm_score(
                        best_node["tok_seq"][-1], best_node["lm_state"])
                    cache_logp.append(log_prob[:-1] + lm_weight * lm_score)
                    cache_lm.append(lm_state)
                else:
                    cache_logp.append(log_prob[:-1])
                # cache stats
                cache_node.append(best_node)
                cache_dec_hid.append(dec_hid)
                cache_dec_out.append(dec_out)

                # set -inf as it already used
                cache_logp[best_idx][best_tok] = MIN_F32
                # init as None and 0
                best_val, best_idx, best_tok = None, 0, 0
                for i, logp in enumerate(cache_logp):
                    max_logp, tok = th.max(logp, dim=-1)
                    if i != 0:
                        # Pr(y^*+k) = Pr(y^*)Pr(k|y^*,t)
                        max_logp += cache_node[i - 1].score
                    else:
                        # init as max_logp
                        best_val = max_logp
                        best_tok = tok.item()
                    if max_logp > best_val:
                        best_val = max_logp
                        best_tok = tok.item()
                        best_idx = i
                if best_idx == 0:
                    best_node = list_a[best_tok]
                else:
                    best_idx -= 1
                    father_node = cache_node[best_idx]
                    update_stats = {
                        "tok_seq":
                            father_node["tok_seq"] + [best_tok],
                        "dec_hid":
                            cache_dec_hid[best_idx],
                        "dec_out":
                            father_node["dec_out"] + [cache_dec_out[best_idx]],
                        "lm_state":
                            cache_lm[best_idx] if with_lm else None
                    }
                    best_idx += 1
                    best_node = Node(best_val, update_stats)
                # sort list_b
                list_b = sorted(list_b, key=lambda n: n.score, reverse=True)
                # end
                if len(list_b) < beam_size:
                    continue
                if list_b[beam_size - 1].score >= best_node.score:
                    list_b = list_b[:beam_size]
                    break
        nbest = min(beam_size, nbest)
        final_hypos = [{
            "score": n.score.item() / (len(n["tok_seq"]) if len_norm else 1),
            "trans": n["tok_seq"] + [self.blank]
        } for n in list_b]
        # return best
        nbest_hypos = sorted(final_hypos,
                             key=lambda n: n["score"],
                             reverse=True)[:nbest]
        logger.info(f"--- beam search gets {len(nbest_hypos)}-best from " +
                    f"{len(list_b)} hypothesis (len_norm = {len_norm}) ...")
        return nbest_hypos
