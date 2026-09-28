# claude-decide

Plugin de Claude Code: un modelo de decisión puntúa **cada** skill, rule, agente y tool MCP frente a
la petición del usuario (y otra vez en cada paso de Claude), y a Claude le llega solo lo que encaja.
Lo que recorta de su contexto se lo devuelve únicamente cuando el modelo lo elige. El modelo de
prueba es [Laya Multilingual](https://huggingface.co/convaiinnovations/laya-multilingual); está
pensado para cambiarlo por uno afinado o por uno mejor.

## Cómo funciona

```
petición / cada herramienta ──► hook.py (UserPromptSubmit, PostToolUse)
                                   └─► daemon.py (modelo cargado una vez, 127.0.0.1:7717)
                                         ├─ catalog.py      skills, rules, agentes y tools MCP activos
                                         ├─ scorer.py       una pregunta sí/no por elemento, en una pasada
                                         └─ contexto        lo que Claude no tiene ya ──► additionalContext
library.py ── recorta ~/.claude/settings.json y ~/.claude/rules, y lo deshace
```

- **Catálogo**: lo que Claude Code carga: `~/.claude/{skills,rules,agents}`, los plugins activados,
  el `.claude` del proyecto y las tools de los servidores MCP configurados (`mcp_catalog.py` las pide
  con `tools/list` por stdio o http y las guarda en caché un día).
- **Puntuación**: estado = la petición; una pregunta `noul` por elemento, con el «sí» medio de cada
  elemento restado (calibración sobre diez peticiones genéricas). Es el patrón del Atlas de
  Text-Decision-Benchmark.
- **Qué se inyecta de cada elegido**:

  | Elemento elegido | Qué recibe Claude |
  |---|---|
  | skill recortada (`name-only`) | su descripción entera |
  | rule en la librería | su texto entero |
  | tool MCP | su descripción y `ToolSearch select:…` para cargarla sin buscar |
  | lo demás | el nombre y la puntuación |

- **Cada paso**: en PostToolUse vuelve a puntuar con «petición + herramienta en curso» y manda como
  mucho 2 elementos que no haya mandado ya en esa petición.
- **Sin daemon**: el hook lo lanza en segundo plano y esa vez no hace nada. Nunca bloquea ni rompe
  una petición.

## Qué se puede recortar y qué no

| | Cómo | Se ahorra |
|---|---|---|
| Skills propias (`~/.claude/skills`) | `skillOverrides: "name-only"`: Claude ve el nombre, puede usarla, y recibe la descripción si se elige | Sí |
| Rules | Se mueven a `~/.claude/library/rules` y entran si se eligen | Sí, pero son fijas por defecto |
| Skills de plugins | Claude Code no les aplica `skillOverrides`; solo se quitan desactivando el plugin entero (y con él sus hooks) | No: solo ranking |
| Agentes | No hay forma de ocultarlos | No: solo ranking |
| Tools MCP | Claude Code ya las difiere; aquí se le dice cuál cargar | Una búsqueda, no contexto |

Las rules son fijas por defecto porque Laya todavía falla lo obvio (con «haz commit», `git-commits`
queda en el puesto 14). `library.py manage rule:x` saca una cuando haga falta.

## Instalación

```bash
claude plugin marketplace add /ruta/a/claude-decide
claude plugin install claude-decide@claude-decide --scope user
python3 library.py apply        # recorta tus skills propias; `restore` lo deshace todo
```

`library.py` guarda lo que cambió en `~/.claude-decide/state.json` y una copia de `settings.json`
anterior a la primera escritura en `~/.claude-decide/settings.json.bak`. Si el usuario ya tenía un
override propio para una skill, no la toca. `apply` se niega si el plugin no está activo: sin su hook,
Claude se quedaría sin descripciones en todas las sesiones.

| Variable (en el `env` de `~/.claude/settings.json`) | Para qué | Por defecto |
|---|---|---|
| `CLAUDE_DECIDE_PYTHON` | Python con `laya` para lanzar el daemon | `.venv/bin/python` del plugin |
| `CLAUDE_DECIDE_HF_HOME` | Caché de Hugging Face donde ya está Laya | la de siempre |
| `CLAUDE_DECIDE_MODE` | `auto` inyecta si hay algo recortado; `inject` siempre; `log` nunca | `auto` |
| `CLAUDE_DECIDE_MODEL` | Modelo de decisión (`MODELS` en `model.py`) o carpeta de un checkpoint de Laya | `laya` |
| `CLAUDE_DECIDE_DEVICE` | `cuda` o `cpu` | el que vea torch |
| `CLAUDE_DECIDE_PORT` | Puerto local del daemon | `7717` |
| `CLAUDE_DECIDE_HOME` | Estado, prior, caché MCP y logs | `~/.claude-decide` |

Cada decisión queda en `~/.claude-decide/logs/decisions.jsonl` (petición o paso → ranking).

## Mediciones

### Acierto (`python bench/run.py`)

Peticiones con los elementos correctos etiquetados y 3 que no deberían activar nada. RTX 4050
Laptop, pregunta en inglés con prior (la mejor variante; la tabla completa la imprime el script).

| Catálogo | hit@1 | hit@3 | recall@5 | MRR | máx. en negativas | ms |
|---|---|---|---|---|---|---|
| 28 elementos (skills, rules), 33 peticiones | 0,43 | 0,60 | 0,57 | 0,57 | 0,90 | 111 |
| 45 elementos (+17 tools MCP), 39 peticiones | 0,36 | 0,56 | 0,53 | 0,50 | 0,91 | ~190 |

Con Laya afinada (`laya-mesa-de-ayuda-v4`, de [Laya-Finetune](https://github.com/zamax14/Laya-Finetune): afinada
con ~11.800 tickets de mesa de ayuda, no con peticiones a Claude Code), sobre 49 elementos y la misma pregunta:

| Modelo | hit@1 | hit@3 | recall@5 | MRR | media de la máxima en negativas | ms |
|---|---|---|---|---|---|---|
| Laya Multilingual (base) | 0,33 | 0,50 | 0,50 | 0,46 | 0,91 | ~230 |
| **laya-mesa-de-ayuda-v4** | **0,36** | **0,53** | **0,57** | **0,51** | **0,58** | ~205 |

v4 gana en todo y, sobre todo, deja de decir «sí» a todo ante «hola». Aun así, ningún umbral sirve: con
0,65 ninguna petición negativa recibe nada, pero 14 de 36 peticiones reales se quedan vacías y la
precisión no pasa de ~0,15. Por eso sigue mandando el top 5 con umbral 0,5.

- **El prior es obligatorio**: sin él, el hit@1 cae a 0,11–0,23.
- **La pregunta en inglés gana**: en español, 0,19–0,20.
- **Una llamada por elemento, como el Atlas**, no mejora y tarda el triple.
- **Las tools MCP salen mejor que las skills**: en los 6 casos MCP, 2 salen primeras y 4 entre las
  3 primeras. Sus descripciones dicen qué hacen; las de muchas skills dicen cuándo usarlas.
- **No sabe decir «nada»**: a «hola» le da ≥0,9 a alguna skill, así que ningún umbral separa una
  petición que no necesita nada de una que sí, y siempre inyecta algo.

### Laya entrenada para esta tarea (`laya-context_prefilter`)

Afinada con [Laya-Finetune](https://github.com/zamax14/Laya-Finetune) sobre 46.763 pares petición–elemento: 9.000
peticiones escritas por qwen3.6:35b para 2.276 skills, agentes, rules y tools MCP de repos públicos y sintéticos, con
negativos cercanos y al azar, y dos jueces (gemma4:31b y qwen3.5:122b). Lee el estado `{"request", "step"}` y la
misma pregunta en inglés, y no usa prior. Mismos 49 elementos y 39 peticiones:

| Modelo | hit@1 | hit@3 | recall@5 | MRR | media de la máxima en negativas | ms (GPU) |
|---|---|---|---|---|---|---|
| laya-mesa-de-ayuda-v4, con prior | 0,36 | 0,53 | 0,57 | 0,51 | 0,58 | ~205 |
| **laya-context_prefilter (1k)** | **0,86** | **1,00** | **0,94** | **0,93** | **0,06** | ~240 |
| laya-context_prefilter (8k) | 0,72 | 0,97 | 0,96 | 0,84 | 0,10 | ~240 |

- **El umbral por fin sirve**: con 0,5 ninguna petición negativa recibe nada, se eligen 2,4 elementos por
  petición de media, con recall 0,82 y precisión 0,45 (v4 no pasaba de ~0,15).
- **Generaliza a catálogos que no vio**: en 3.120 pares de fuentes reservadas (otros repos y servidores MCP),
  acierta el 90 % (Laya base, 41 %), con Brier 0,078.
- **El formato del estado importa**: con la petición como texto suelto baja a hit@3 0,89 y 0,25 en negativas.
- **El prior sobra**: con él, las negativas suben a 0,57; `scorer.calibrate` lo omite para este modelo.
- **En CPU es lento**: ~4,6 s por petición con 49 elementos (una secuencia por pregunta); en GPU, ~240 ms.
- El de 8k solo compensa si se le pasa más contexto que la petición; en peticiones cortas es peor.

### Contexto (`claude -p --output-format json`, «responde solo: ok»)

| | Tokens de entrada |
|---|---|
| Sin plugin | 26.335 |
| Plugin instalado, sin recorte (modo `auto` no inyecta) | 26.335 |
| `library.py apply` (4 skills propias a solo nombre), incluidas ~180 de inyección | 25.833 |

El recorte quita ~680 tokens y la inyección devuelve ~180. Las descripciones de skills de plugins,
que no se pueden recortar, suman ~1.900 tokens fijos (`claude plugin details`: superpowers ~840,
ponytail ~985, frontend-design ~80). Aun recortándolo todo, el techo en esta máquina está en torno
al 10 % del contexto fijo, y ese contexto ya se cobra casi siempre como lectura de caché.

### Comprobado en una sesión real

- Una skill en «solo nombre» se sigue pudiendo invocar con la herramienta Skill.
- Claude solo ve su nombre, y la descripción le llega por claude-decide cuando se elige.
- PostToolUse inyecta las novedades tras cada herramienta, aunque con el ruido de Laya.

## Cambiar de modelo

Un checkpoint de Laya afinado se usa tal cual: `CLAUDE_DECIDE_MODEL=/ruta/al/checkpoint` (la carpeta con
`rl_agent_config.json`, el formato de `laya.load`). Cada modelo calibra su propio prior la primera vez, salvo
uno entrenado con la tarea `context_prefilter` de Laya-Finetune (`model_name: laya-context_prefilter` en su
`rl_agent_config.json`): ese recibe el estado `{"request", "step"}` y se usa sin prior.

Cualquier otro objeto con `name` y `predict(state, questions)` que devuelva respuestas con forma de Laya
(`{"answers": {id: {"noul": p}}}`) sirve. Se añade a `MODELS` en `model.py`, se elige con
`CLAUDE_DECIDE_MODEL` y se compara con `python bench/run.py`. `remote.py` de Text-Decision-Benchmark
ya adapta LLMs de OpenRouter a esa forma. El log de decisiones y lo que Claude usó de verdad en cada
sesión son los pares para afinarlo.

## Límites conocidos

- Las skills y agentes integrados en Claude Code no están en disco y no entran en el catálogo.
- De un plugin solo se leen `skills/` y `agents/`; los servidores MCP que traen los plugins no entran.
- Los conectores de claude.ai y la extensión de Chrome no se pueden listar en local.
- Un `.mcp.json` de proyecto entra aunque el usuario no lo haya aprobado en Claude Code.

## Pruebas

```bash
python3 -m unittest discover -s tests   # modelos falsos y carpetas temporales, sin GPU ni ~/.claude
```
