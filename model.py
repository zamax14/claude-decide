"""El modelo de decisión, intercambiable.

Contrato: un objeto con `name` y `predict(state, questions)` que devuelve respuestas con forma de
Laya (`{"answers": {qid: {"noul": p, ...}}}`). Para probar otro modelo (uno afinado, un LLM
detrás de una API) basta con otra clase con ese método y una entrada en `MODELS`; se elige con
la variable `CLAUDE_DECIDE_MODEL`.
"""
import os
import threading

# torch lo lee al importarse: sin él, algunas operaciones de GPU compilan con Triton y piden Python.h.
os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")
os.environ.setdefault("USE_TF", "0")

LAYA = "convaiinnovations/laya-multilingual@82d57fc4f2d1be3d2caac494045f2ec51d0842f3"


class Laya:
    """Laya Multilingual, cargada una vez y usada en serie (el tokenizador no admite concurrencia)."""
    name = "laya-multilingual"

    def __init__(self, max_len=1024, head_max_len=256, device=None):
        self.max_len, self.head_max_len = max_len, head_max_len
        self.lock = threading.Lock()
        self.agent = self._load(device or os.environ.get("CLAUDE_DECIDE_DEVICE"))

    def _load(self, device):
        import laya
        from huggingface_hub import snapshot_download
        try:
            from transformers.initialization import no_init_weights
        except ImportError:  # transformers < 5 lo exponía en modeling_utils.
            from transformers.modeling_utils import no_init_weights
        repo, revision = LAYA.split("@")
        path = snapshot_download(repo, revision=revision,
                                 allow_patterns=["rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*"])
        # Sin el contexto, el encoder se rellena al azar antes de cargar los pesos (~14 s en CPU en vez de ~1,4 s).
        with no_init_weights():
            agent = laya.load(path, device=device)
        agent.cfg["max_len"], agent.cfg["head_max_len"] = self.max_len, self.head_max_len
        agent.predict("Hola", {"calentar": {"type": "noul", "instructions": "¿Es un saludo?"}})
        return agent

    def predict(self, state, questions):
        from laya.common import build_sequence
        with self.lock:
            # Laya trunca el estado sin avisar; aquí se rechaza antes de inferir.
            for question in questions.values():
                full, _ = build_sequence(self.agent.tok, state, self.agent._to_internal(question),
                                         10**6, self.head_max_len)
                if len(full) > self.max_len:
                    raise ValueError(f"El estado no cabe en el contexto: {len(full)} de {self.max_len} tokens")
            return self.agent.predict(state, questions)


MODELS = {"laya": Laya}


def load_model(name=None):
    return MODELS[name or os.environ.get("CLAUDE_DECIDE_MODEL", "laya")]()
