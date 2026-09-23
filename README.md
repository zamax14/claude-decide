# claude-decide

Plugin de Claude Code: un modelo de decisión puntúa **cada** skill, rule y agente del catálogo frente
a la petición del usuario, y a Claude le llega la lista ordenada de lo que encaja, sin que tenga que
leer descripciones ni elegir. El modelo de prueba es [Laya Multilingual](https://huggingface.co/convaiinnovations/laya-multilingual);
está pensado para cambiarlo por uno afinado o por uno mejor.

**Estado: fase 1, observación.** Puntúa y apunta cada decisión, pero por defecto no inyecta nada ni
quita nada del contexto. Con Laya sin afinar, las sugerencias todavía no son fiables (ver Mediciones).

## Cómo decide

```
petición ──► hook.py (UserPromptSubmit) ──► daemon.py (modelo cargado una vez)
                                               ├─ catalog.py: skills, rules y agentes que carga Claude Code
                                               ├─ scorer.py: una pregunta sí/no por elemento, todas en una pasada
                                               └─ ranking ──► logs/decisions.jsonl  (+ contexto de Claude en modo inject)
```

- **Catálogo**: lo mismo que ve Claude Code: `~/.claude/{skills,rules,agents}`, los plugins activados
  en los settings y el `.claude` del proyecto. Las skills con `disable-model-invocation: true` no
  entran: Claude tampoco las ve.
- **Puntuación**: estado = la petición; una pregunta `noul` por elemento («Is this Claude Code skill
  useful for handling the user's request? …»). A cada elemento se le resta su «sí» medio sobre diez
  peticiones de calibración (`prior.json`, se recalcula solo si cambia la descripción). Es el patrón
  del Atlas de Text-Decision-Benchmark.
- **Hook**: si el daemon no responde, lo lanza en segundo plano y esa vez no hace nada. Nunca bloquea
  ni rompe una petición.

## Instalación

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # o apunta a un entorno que ya tenga laya
claude --plugin-dir /ruta/a/claude-decide
```

| Variable | Para qué | Por defecto |
|---|---|---|
| `CLAUDE_DECIDE_PYTHON` | Python con `laya` para lanzar el daemon | `.venv/bin/python` |
| `CLAUDE_DECIDE_MODE` | `log` solo apunta; `inject` le pasa la lista a Claude | `log` |
| `CLAUDE_DECIDE_MODEL` | Modelo de decisión (`MODELS` en `model.py`) | `laya` |
| `CLAUDE_DECIDE_DEVICE` | `cuda` o `cpu` | el que vea torch |
| `CLAUDE_DECIDE_PORT` | Puerto local del daemon | `7717` |
| `HF_HOME` | Caché de Hugging Face, si Laya ya está descargada en otro sitio | la de siempre |

Van en el `env` de `~/.claude/settings.json` o en la shell que abre Claude Code.

## Mediciones

`python bench/run.py`: 30 peticiones con los elementos correctos etiquetados y 3 que no deberían
activar nada, sobre los 28 elementos de esta máquina. RTX 4050 Laptop.

| Variante | Prior | hit@1 | hit@3 | recall@5 | MRR | máx. en negativas | ms |
|---|---|---|---|---|---|---|---|
| Una pasada, pregunta en inglés | sí | **0,43** | **0,60** | 0,57 | **0,57** | 0,90 | 111 |
| Una pasada, pregunta en inglés | no | 0,23 | 0,40 | 0,54 | 0,38 | 0,95 | 113 |
| Una pasada, pregunta en español | sí | 0,20 | 0,50 | 0,53 | 0,39 | 0,85 | 113 |
| Una llamada por elemento (como el Atlas) | sí | 0,27 | 0,60 | 0,64 | 0,45 | 0,61 | 314 |

Al azar, hit@1 rondaría 0,05 y hit@3 0,15. Otras dos redacciones de la pregunta en inglés quedaron
por debajo (hit@1 0,30 y 0,33).

Lo que dicen los números:

- **Rápido de sobra**: ~110 ms por petición para todo el catálogo. El cuello no es la latencia.
- **El prior es obligatorio**: duplica el hit@1. Sin él, las skills con descripciones como «You MUST
  use this before any creative work» copan el ranking.
- **No sabe decir «nada»**: a «hola» le da 0,95 a alguna skill. Ningún umbral separa una petición
  que no necesita nada de una que sí; por eso manda el top 5.
- **Falla en lo obvio**: con «haz commit de estos cambios», la regla `git-commits` queda en el puesto 14.
- **Precisión baja**: con el top 5, solo ~1 de cada 5 sugerencias es correcta. Por eso el modo por
  defecto es `log`.

Conclusión: la tubería funciona de punta a punta; el modelo sin afinar es lo que falta. El log de
`logs/decisions.jsonl` (petición → ranking) más lo que Claude usó de verdad en cada sesión son justo
los pares que hacen falta para afinarlo.

## Cambiar de modelo

Cualquier objeto con `name` y `predict(state, questions)` que devuelva respuestas con forma de Laya
(`{"answers": {id: {"noul": p}}}`) sirve. Se añade a `MODELS` en `model.py`, se elige con
`CLAUDE_DECIDE_MODEL` y se compara con `python bench/run.py`. `remote.py` de Text-Decision-Benchmark
ya adapta LLMs de OpenRouter a esa forma.

## Límites conocidos

- Las skills y agentes integrados en Claude Code (no están en disco) no entran en el catálogo.
- De un plugin solo se leen `skills/` y `agents/`; rutas propias declaradas en su `plugin.json` no.
- Fase 2 (recortar de verdad el contexto) y fase 3 (tools MCP y repuntuar en cada paso con
  `PostToolUse`) están sin hacer.

## Pruebas

```bash
python3 -m unittest discover -s tests   # con un modelo falso, sin GPU
```
