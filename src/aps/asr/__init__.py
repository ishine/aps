from .las_asr import LasASR
from .enh_las_asr import MvdrLasASR, BeamLasASR
from .transformer_asr import TransformerASR
from .enh_transformer_asr import BeamTransformerASR, MvdrTransformerASR
from .transducer_asr import TransformerTransducerASR, TorchTransducerASR
from .lm.rnn import TorchRNNLM
from .lm.transformer import TorchTransformerLM

nnet_cls = {
    "rnn_lm": TorchRNNLM,
    "transformer_lm": TorchTransformerLM,
    "las": LasASR,
    "mvdr_las": MvdrLasASR,
    "beam_las": BeamLasASR,
    "transformer": TransformerASR,
    "beam_transformer": BeamTransformerASR,
    "mvdr_transformer": MvdrTransformerASR,
    "transformer_transducer": TransformerTransducerASR,
    "common_transducer": TorchTransducerASR
}


def support_nnet(nnet_type):
    if nnet_type not in nnet_cls:
        raise RuntimeError(f"Unsupported network type: {nnet_type}")
    return nnet_cls[nnet_type]
