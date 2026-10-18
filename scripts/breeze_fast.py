"""Batched generation for Breeze TTS 2 (mlx-audio's MLX build), used by render_audio.py.

Runs in the voice engine's venv, never under the system Python. mlx-audio's
Model.generate voices one line at a time. Every sound frame (1/12.5 s) runs the 28-layer
backbone once and the 12-layer depth decoder 15 times, once per codebook, re-reading the
whole decoder each time. A delivery note (classifier-free guidance) runs all of that twice,
with and without the note. Each codebook token also goes back to the CPU as a Python int.
The weights are read from memory far more often than the arithmetic needs, so batching is
nearly free:
- a line's guided and unguided passes run as one batch of two, in the backbone and the
  depth decoder;
- several lines run together: their prompts are prefilled one at a time (exactly as
  generate does) and then merged into one left-padded cache, so each line keeps its own
  positions and masks;
- the depth decoder keeps a KV cache instead of re-running its whole input per codebook;
- sampled tokens stay on the GPU, and a frame is checked for end-of-speech only after the
  next one has been queued, so the CPU never stalls the GPU.

Sampling is the model's own: the same masks, temperature, top-k and top-p, one draw per
token. With one line and the same seed, the random draws come in the same order as
generate's. Tokens can still differ after a while: batched matrix products round in a
different order, and a near-tie between two tokens can then go the other way. Lines
batched together share each draw's random key, so they differ from one-at-a-time renders
the way two seeds do.
"""

import os

import mlx.core as mx
import mlx.nn as nn
import numpy as np
from mlx_audio.lm.models.cache import BatchKVCache, KVCache
from mlx_audio.lm.sample_utils import make_sampler
from mlx_audio.tts.models.breeze_tts.breeze_tts import _split_long_segment


class _DepthCache(KVCache):
    step = 16  # a frame's depth pass is never longer than 16 tokens


def segments(text):
    """generate's own split (on newlines, then at 600 characters); each part is voiced separately."""
    parts = []
    for s in (s.strip() for s in text.split("\n")):
        if s:
            parts.extend(_split_long_segment(s))
    return parts or [text]


class BatchedBreeze:
    def __init__(self, model, depth_cache=True):
        """depth_cache=False re-runs the depth decoder over the whole frame at every codebook, as
        generate does. It's slow when batched and only there to check tokens against generate's."""
        self.model = model
        self.depth_cache = depth_cache
        self._refs = {}

    def _reference(self, ref_audio):
        """The reference clip's codec tokens, encoded once per file (generate encodes it per line)."""
        if not isinstance(ref_audio, (str, os.PathLike)):
            return self.model._encode_reference(ref_audio)
        key = (os.fspath(ref_audio), os.path.getmtime(ref_audio))
        if key not in self._refs:
            self._refs[key] = self.model._encode_reference(ref_audio)
            mx.eval(self._refs[key])
        return self._refs[key]

    def _prompt(self, text, voice, instruct, ref_audio, ref_text):
        """Model._prompt_embeddings, with the reference's tokens cached."""
        m = self.model
        if ref_audio is not None and not ref_text:
            raise ValueError("Breeze voice cloning requires ref_text with ref_audio.")
        speaker = m._speaker(voice)
        parts = []
        if ref_audio is not None:
            parts.append(m.text_encoder_proj(m.text_encoder(m._text_ids(speaker + ref_text)[None, :])))
            parts.append(m.backbone_model.embed_tokens(self._reference(ref_audio)))
            eos = mx.full((1, 1, m.num_codebooks), m.config.codebook_eos_token_id)
            parts.append(m.backbone_model.embed_tokens(eos))
        target = "%s<ins_bos>%s<ins_eos>%s" % (speaker, instruct, text) if instruct else speaker + text
        parts.append(m.text_encoder_proj(m.text_encoder(m._text_ids(target)[None, :])))
        return mx.concatenate(parts, axis=1)

    def _sample(self, logits, sampler, allow_eos):
        # Model._sample without the trip to the CPU: logits [rows, vocab] -> tokens [rows].
        m = self.model
        valid = m.vocab_size + 1 if allow_eos else m.vocab_size
        logits = m._mask_reserved_codec_logits(logits)[..., :valid]
        return sampler(nn.log_softmax(logits, axis=-1))

    def _depth(self, tokens, hidden, caches, codebook):
        """One depth-decoder step for every row: the newest token in, the last position's state out.
        Codebook 0's step also feeds the backbone state, as the placeholder position."""
        dm = self.model.depth_decoder.model
        embeds = dm.embed_tokens(tokens + codebook * dm.vocab_size)[:, None, :]
        if codebook == 0:
            if dm.backbone_hidden_state_projector is not None:
                hidden = dm.backbone_hidden_state_projector(hidden)
            embeds = mx.concatenate([hidden[:, None, :], embeds], axis=1)
        h = dm.inputs_embeds_projector(embeds)
        mask = "causal" if h.shape[1] > 1 else None
        for layer, cache in zip(dm.layers, caches):
            h = layer(h, mask, cache)
        return dm.norm(h)[:, -1, :]

    def generate(self, items, max_tokens=750, temperature=0.9, top_p=1.0, top_k=50, seed=None):
        """items: dicts with text, and optionally voice, instruct, ref_audio, ref_text, cfg_scale
        (guidance is used only with an instruct, as in generate). Returns one float32 numpy
        array per item at model.sample_rate, its segments joined like generate's results."""
        m = self.model
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive.")
        if seed is not None:
            mx.random.seed(seed)
        seqs = []  # (item index, segment text)
        for i, it in enumerate(items):
            seqs += [(i, s) for s in segments(it["text"])]
        n = len(seqs)
        # Rows: one per sequence (with its note, if any), then an unguided row for each guided one.
        scales, guided, embeds = [], [], []
        for k, (i, text) in enumerate(seqs):
            it = items[i]
            embeds.append(self._prompt(text, it.get("voice"), it.get("instruct"), it.get("ref_audio"), it.get("ref_text")))
            scale = it.get("cfg_scale")
            use = bool(it.get("instruct")) and scale not in (None, 1.0)
            guided.append(use)
            scales.append(scale if use else 1.0)
        uncond_rows = list(range(n))
        for k, (i, text) in enumerate(seqs):
            if guided[k]:
                it = items[i]
                uncond_rows[k] = len(embeds)
                embeds.append(self._prompt(text, it.get("voice"), None, it.get("ref_audio"), it.get("ref_text")))
        row_seq = mx.array(list(range(n)) + [k for k in range(n) if guided[k]])
        any_guided = any(guided)
        uncond_idx = mx.array(uncond_rows)
        guided_mask = mx.array(guided)[:, None]

        # Prefill each row on its own, as generate does, then merge into one left-padded cache.
        backbone = m.backbone_model
        per_row, hidden = [], []
        for e in embeds:
            cache = backbone.make_cache()
            h = backbone(input_embeddings=e, cache=cache)[:, -1, :]
            mx.eval(h, [(c.keys, c.values) for c in cache])
            per_row.append(cache)
            hidden.append(h)
        caches = [BatchKVCache.merge([c[layer] for c in per_row]) for layer in range(len(backbone.layers))]
        del per_row
        hidden = mx.concatenate(hidden, axis=0)

        top_k_eff = min(top_k, m.vocab_size + 1) if top_k else 0
        sampler_eos = make_sampler(temp=temperature, top_p=top_p,
                                   top_k=0 if top_k_eff == m.vocab_size + 1 else top_k_eff)
        top_k_eff = min(top_k, m.vocab_size) if top_k else 0
        sampler = make_sampler(temp=temperature, top_p=top_p, top_k=0 if top_k_eff == m.vocab_size else top_k_eff)
        dtype = hidden.dtype
        scale_col = mx.array(scales, dtype=dtype)[:, None]
        heads = m.depth_decoder.codebooks_head.weight

        def guide(logits):
            # [rows, vocab] -> [n, vocab]: unguided + scale * (guided - unguided) where there's a note.
            cond = logits[:n]
            if not any_guided:
                return cond
            uncond = logits[uncond_idx]
            return mx.where(guided_mask, uncond + scale_col * (cond - uncond), cond)

        def frame(hidden, done):
            logits = m._mask_reserved_codec_logits(guide(m.lm_head(hidden)))
            first = self._sample(logits, sampler_eos, allow_eos=True)
            done = done | (first == m.vocab_size)
            codes = [first]
            dcaches = [_DepthCache() for _ in m.depth_decoder.model.layers]
            token = first[row_seq]
            history = [mx.zeros_like(token), token]  # only for depth_cache=False
            for cb in range(m.num_codebooks - 1):
                if self.depth_cache:
                    h = self._depth(token, hidden, dcaches, cb) @ heads[cb]
                else:
                    h = m.depth_decoder.next_logits(mx.stack(history, axis=1).astype(mx.int32), hidden)
                logits = m._mask_reserved_codec_logits(guide(h))
                tok = self._sample(logits, sampler, allow_eos=False)
                codes.append(tok)
                token = tok[row_seq]
                history.append(token)
            codes = mx.stack(codes, axis=1).astype(mx.int32)
            nxt = backbone(input_ids=codes[row_seq][:, None, :], cache=caches)[:, -1, :]
            return codes, nxt, done

        done = mx.zeros((n,), dtype=mx.bool_)
        frames, alive = [], []
        prev_all_done = None
        for _ in range(max_tokens):
            codes, hidden, done = frame(hidden, done)
            all_done = mx.all(done)
            mx.async_eval(codes, hidden, done, all_done)
            frames.append(codes)
            alive.append(~done)
            # Check the previous frame while this one runs on the GPU.
            if prev_all_done is not None and prev_all_done.item():
                frames.pop()
                alive.pop()
                break
            prev_all_done = all_done
        frames = mx.stack(frames, axis=0)  # [time, n, codebooks]
        lengths = mx.sum(mx.stack(alive, axis=0).astype(mx.int32), axis=0).tolist()

        out = [[] for _ in items]
        for k, (i, _) in enumerate(seqs):
            if lengths[k]:
                audio = m._decode_codes(frames[: lengths[k], k, :][None])
            else:
                audio = m._empty_audio()
            mx.eval(audio)
            out[i].append(np.asarray(audio, dtype="float32").reshape(-1))
        return [np.concatenate(parts) if parts else np.zeros(0, dtype="float32") for parts in out]
