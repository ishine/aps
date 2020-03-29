#!/usr/bin/env python

# wujian@2020

#!/usr/bin/env python

# wujian@2019

import torch as th
import torch.nn as nn
import torch.nn.functional as F

from .transformer.encoder import TorchTransformerEncoder
from .transducer.decoder import TorchTransformerDecoder
from .las.encoder import encoder_instance


class TransformerTransducerASR(nn.Module):
    """
    Transducer end-to-end ASR (transformer as decoder)
    """
    def __init__(self,
                 input_size=80,
                 vocab_size=40,
                 sos=-1,
                 eos=-1,
                 asr_transform=None,
                 encoder_type="transformer",
                 encoder_proj=None,
                 encoder_kwargs=None,
                 decoder_type="transformer",
                 decoder_kwargs=None):
        super(TransformerTransducerASR, self).__init__()
        if eos < 0 or sos < 0:
            raise RuntimeError(f"Unsupported SOS/EOS value: {sos}/{eos}")
        if decoder_type != "transformer":
            raise ValueError(
                "TransformerTransducerASR: decoder must be transformer")
        if encoder_type == "transformer":
            self.encoder = TorchTransformerEncoder(input_size,
                                                   **encoder_kwargs)
        else:
            if encoder_proj is None:
                raise ValueError("For non-transformer encoder, "
                                 "encoder_proj can not be None")
            self.encoder = encoder_instance(encoder_type, input_size,
                                            encoder_proj, **encoder_proj)
        self.decoder = TorchTransformerDecoder(vocab_size,
                                               enc_dim=encoder_proj,
                                               **decoder_kwargs)
        self.sos = sos
        self.eos = eos
        self.asr_transform = asr_transform
        self.encoder_type = encoder_type

    def forward(self, x_pad, x_len, y_pad, y_len):
        """
        args:
            x_pad: N x Ti x D or N x S
            x_len: N or None
            y_pad: N x To
            y_len: N or None
        return:
            outs: N x (To+1) x V
        """
        # feature transform
        if self.asr_transform:
            x_pad, x_len = self.asr_transform(x_pad, x_len)
        # Ti x N x D or N x Ti x D
        enc_out, enc_len = self.encoder(x_pad, x_len)
        if self.encoder_type != "transformer":
            # N x Ti x D => Ti x N x D
            enc_out = enc_out.transpose(0, 1)
        # N x Ti x To+1 x V
        dec_out = self.decoder(enc_out, y_pad, y_len, sos=self.sos)
        return dec_out, enc_len