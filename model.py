"""El modelo de decisión, intercambiable.

Contrato: un objeto con `name` y `predict(state, questions)` que devuelve respuestas con forma de
Laya (`{"answers": {qid: {"noul": p, ...}}}`). Para probar otro modelo (uno afinado, un LLM
detrás de una API) basta con otra clase con ese método y una entrada en `MODELS`; se elige con
la variable `CLAUDE_DECIDE_MODEL`, que también acepta la carpeta de un checkpoint de Laya afinado
(el formato de `laya.load`, p. ej. los de Laya-Finetune) o un repo de Hugging Face (`usuario/modelo[@revisión]`).
Por defecto, `prefilter`: Laya afinada para esta tarea, descargada de Hugging Face la primera vez.
"""
import os
import threading
from pathlib import Path

# torch lo lee al importarse: sin él, algunas operaciones de GPU compilan con Triton y piden Python.h.
os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")
os.environ.setdefault("USE_TF", "0")

LAYA = "convaiinnovations/laya-multilingual@82d57fc4f2d1be3d2caac494045f2ec51d0842f3"
# Nombre que Laya-Finetune da a un checkpoint entrenado con su tarea context_prefilter: la pregunta de scorer.py,
# con el estado {"request", "step"}. Ese modelo ya sale calibrado y no necesita el prior.
PREFILTER = "laya-context_prefilter"
PREFILTER_REPO = "Zamax14/laya-context-prefilter"


class Laya:
    """Laya, cargada una vez y usada en serie (el tokenizador no admite concurrencia).

    Sin `checkpoint`, Laya Multilingual base; con él, esa carpeta local o ese repo de Hugging Face. El nombre entra en
    la clave del prior, así que cada checkpoint calibra el suyo. `prefilter`: entrenado para esta tarea.
    """

    def __init__(self, checkpoint=None, max_len=1024, head_max_len=256, device=None):
        self.checkpoint = checkpoint
        self.name = Path(checkpoint.split("@")[0]).name if checkpoint else "laya-multilingual"
        self.max_len, self.head_max_len = max_len, head_max_len
        self.lock = threading.Lock()
        self.agent = self._load(device or os.environ.get("CLAUDE_DECIDE_DEVICE"))
        self.prefilter = self.agent.cfg.get("model_name") == PREFILTER

    def _load(self, device):
        import laya
        from huggingface_hub import snapshot_download
        try:
            from transformers.initialization import no_init_weights
        except ImportError:  # transformers < 5 lo exponía en modeling_utils.
            from transformers.modeling_utils import no_init_weights
        path = self.checkpoint or LAYA
        if not Path(path).expanduser().is_dir():  # Un repo de Hugging Face, con revisión opcional.
            repo, _, revision = path.partition("@")
            path = snapshot_download(repo, revision=revision or None,
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


MODELS = {"prefilter": lambda: Laya(PREFILTER_REPO), "laya": Laya}


def load_model(name=None):
    name = name or os.environ.get("CLAUDE_DECIDE_MODEL", "prefilter")
    if name in MODELS:
        return MODELS[name]()
    if (Path(name).expanduser() / "rl_agent_config.json").is_file():
        return Laya(checkpoint=str(Path(name).expanduser()))
    if "/" in name and not Path(name).expanduser().exists():  # usuario/modelo en Hugging Face.
        return Laya(checkpoint=name)
    raise ValueError(f"CLAUDE_DECIDE_MODEL={name}: ni un modelo de MODELS, ni una carpeta de checkpoint de Laya, "
                     "ni un repo de Hugging Face")
