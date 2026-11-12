# LLM-RSS — Plan

Filtro diario de noticias con LLM. Lee muchas fuentes (RSS/APIs), descarta la paja con Claude
y publica un feed RSS propio con solo lo que apunta a **ideas revolucionarias** (novedad real,
cambio de fondo, evidencia tangible). Corre en **GitHub Actions**, guarda el estado (SQLite) en
la **cache de Actions** y se configura entero por **variables de entorno** de Actions.

## Decisiones tomadas

- Repo **público** (minutos de Actions y Pages gratis). Feed público; logs sin config/perfil/prompts.
- Salida del LLM (`why`, análisis semanal) en **inglés**.
- `score` lo calcula el código, no el LLM:
  `score = clamp(0.35·novelty + 0.35·impact + 0.30·evidence − 0.25·hype + 0.5·(fuentes−1)) · weight`
  (coeficientes configurables en `scoring:`).
- Descarga del texto del artículo: **aplazada** (idea futura). Se usa título + resumen del feed.
- Overrides por envvar con ruta anidada: `LLMRSS__SELECTION__THRESHOLD=8`
  (doble guion bajo = nivel). `LLMRSS_CONFIG`, `LLMRSS_DRY_RUN`, `LLMRSS_CACHE_HIT` son reservadas.
- `claude -p` sin herramientas y a un turno (`--tools "" --max-turns 1`): el contenido de los
  feeds no es de fiar (prompt injection).
- Tags: se pasan al prompt los tags existentes y se pide reutilizarlos; se normalizan
  (minúsculas, kebab-case, tabla de alias en config `tag_aliases`).
- Semanal: se genera si han pasado ≥7 días desde el último, no "si es domingo".
- HN sin URL externa (Ask/Show HN): url = hilo de HN.

---

## 1. Objetivo y no-objetivos

- Objetivo: de cientos de entradas/día → 3–10 entradas con "por qué importa".
  Además, un feed semanal de **tendencias**: temas que se repiten y ganan fuerza.
- Objetivo: todo configurable sin tocar código (fuentes, perfil, rúbrica, umbrales, modelos).
- No-objetivo: consejo de inversión, UI web, multiusuario.

## 2. Arquitectura

```
fetch (fuentes) → normalizar → dedup (SQLite) → prefiltro barato (LLM por lotes, solo títulos)
  → puntuación profunda (LLM por lotes, rúbrica) → selección (umbral + tope diario)
  → render feed.xml / weekly.xml / index.html → GitHub Pages
```

- Python ≥ 3.11, gestionado con `uv`. Paquete `llmrss` con CLI `llmrss`.
- Dependencias: `feedparser`, `httpx`, `pyyaml`, `pydantic` (validar config y salidas del LLM),
  `feedgen` (o RSS a mano con `xml.etree`), `tenacity` (reintentos).
- LLM: `claude -p --model <m> --output-format json` (Claude Code CLI) autenticado con la
  **suscripción** vía `CLAUDE_CODE_OAUTH_TOKEN` (se obtiene en local con `claude setup-token`).
  Encapsular en `llm.py` para poder cambiar de backend (API key) sin tocar el resto.

### Estructura

```
LLM-RSS/
  pyproject.toml
  src/llmrss/
    cli.py          # run | weekly | validate-config | dry-run | rebuild-feed
    config.py       # carga LLMRSS_CONFIG (YAML/JSON) + overrides por envvar, valida con pydantic
    sources/        # rss.py, hn.py, github_trending.py, arxiv.py (interfaz común)
    normalize.py    # url canónica, quitar utm_*, hash de título
    store.py        # SQLite: esquema, migraciones simples
    llm.py          # wrapper de `claude -p`, lotes, parseo/validación JSON, reintentos
    prefilter.py
    score.py
    trends.py
    render.py       # feed.xml, weekly.xml, index.html
    prompts/        # prefilter.md, score.md, weekly.md (por defecto; sobreescribibles en config)
  tests/
  .github/workflows/llmrss.yml
  .github/workflows/keepalive.yml
  config.example.yaml
```

## 3. Configuración (envvars de Actions)

Una **variable de repositorio** `LLMRSS_CONFIG` con YAML (o JSON) multilínea
(límite 48 KB por variable, de sobra). Secretos aparte.

| Envvar | Tipo Actions | Uso |
|---|---|---|
| `LLMRSS_CONFIG` | variable | Config completa (YAML/JSON) |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret | Auth de `claude -p` con la suscripción |
| `GITHUB_TOKEN` | automático | Llamadas a la API de GitHub (trending/estrellas) |
| `LLMRSS__<RUTA>` (p. ej. `LLMRSS__MODELS__SCORE`) | opcional | Override de cualquier clave (anidada con `__`) |

Ejemplo (`config.example.yaml`):

```yaml
profile: |
  Ingeniero de firmware / low-level / Rust. Me interesan ideas que cambian las reglas:
  nuevos paradigmas de computación, materiales, energía, biotech, hardware abierto,
  protocolos, herramientas que reducen costes en órdenes de magnitud.
  NO me interesan: reviews de producto, rumores, política, finanzas, lanzamientos incrementales.

models:
  prefilter: claude-haiku-5-5
  score: claude-opus-5-5
  weekly: claude-opus-5-5

selection:
  threshold: 5            # puntuación mínima 0–10 (calibrado: 7 casi no se alcanza)
  max_per_day: 10
  min_per_day: 0          # no rellenar si no hay nada bueno

batching:
  prefilter_batch: 80     # títulos por llamada
  score_batch: 15         # entradas con resumen por llamada
  max_llm_calls_per_run: 40

feed:
  title: "LLM-RSS"
  link: "https://<usuario>.github.io/<repo>/"
  description: "Lo que importa, sin paja"
  keep_entries: 300

retention_days: 365       # histórico para tendencias

sources:
  - {name: hackaday,  type: rss, url: "https://hackaday.com/blog/feed/"}
  - {name: hn-front,  type: hn,  list: front_page, min_points: 100}
  - {name: show-hn,   type: hn,  list: show, min_points: 5}
  - {name: lobsters,  type: rss, url: "https://lobste.rs/rss"}
  - {name: cnx,       type: rss, url: "https://www.cnx-software.com/feed/"}
  - {name: spectrum,  type: rss, url: "https://spectrum.ieee.org/feeds/feed.rss"}
  - {name: quanta,    type: rss, url: "https://www.quantamagazine.org/feed/"}
  - {name: arxiv-cs-ar, type: rss, url: "https://rss.arxiv.org/rss/cs.AR", prefilter: true}
  - {name: arxiv-cond-mat, type: rss, url: "https://rss.arxiv.org/rss/cond-mat.mtrl-sci", prefilter: true}
  - {name: gh-trending, type: github_trending, language: "", since: daily}

# Opcional: sobreescribir prompts/rúbrica por defecto
# rubric: |
#   ...
```

- `prefilter: true|false` por fuente (por defecto true en fuentes de alto volumen).
- `weight` opcional por fuente (sesgo en la puntuación final).
- `llmrss validate-config` valida la config y lista fuentes alcanzables; se ejecuta como
  primer paso del workflow.
- **No loguear nunca la config ni el perfil**: en repo público los logs de Actions son públicos.

## 4. Fuentes

Interfaz: `fetch(cfg) -> list[Item]` con
`Item(source, url, title, summary, published_at, extra: dict)`.

- `rss`: feedparser genérico (cubre Hackaday, Lobsters, arXiv, blogs…).
- `hn`: API Algolia (`front_page`, `show_hn`, `story` con `numericFilters=points>N`).
- `github_trending`: scraping de la página trending (no hay API oficial). Guardar las estrellas
  en `extra` para detectar aceleración entre ejecuciones.
- Cada fuente aislada: si una falla, se loguea y sigue (no tumbar la ejecución).
- Timeouts y User-Agent genérico (`llmrss/0.1`), sin datos personales.

## 5. Estado (SQLite)

`state/llmrss.db`:

- `items(id PK = hash(url canónica), source, url, title, summary, published_at, fetched_at, title_hash, seen_in_sources JSON)`
- `prefilter(item_id, keep bool, model, ts)`
- `scores(item_id, novelty, impact, evidence, hype, score, category, tags JSON, why, model, ts)`
- `published(item_id, feed, ts)`: lo que entró en el feed (para reconstruirlo).
- `tag_counts(tag, day, count)`: base para las tendencias.
- `runs(ts, items_fetched, new, prefiltered, scored, published, llm_calls, errors)`

Dedup: url canónica (sin `utm_*`, sin fragmento, `arxiv abs/pdf` unificado) y, además, hash del
título normalizado (la misma noticia en varias fuentes cuenta como **señal**: se guarda
`seen_in_sources` y sube la prioridad).

## 6. LLM

### Prefiltro (barato, solo títulos + fuente)
Lotes de ~80. Devuelve `[{id, keep}]`. Descarta lo obvio (reviews, ofertas, política,
incremental). Debe ser **permisivo**: ante la duda, mantener.

### Puntuación (rúbrica)
Lotes de ~15 con título + resumen .
Salida JSON validada con pydantic:

```json
{"id":"…","novelty":0-10,"impact":0-10,"evidence":0-10,"hype":0-10,
 "score":0-10,"category":"materiales|computación|energía|bio|hardware|software|otro",
 "tags":["…"],"why":"2-3 frases: qué es y por qué podría importar"}
```

Rúbrica por defecto:
- **Novedad**: ¿es nuevo de verdad o variación de algo conocido?
- **Impacto**: ¿cambia coste, capacidad o escala en órdenes de magnitud? ¿Abre un campo?
- **Evidencia**: ¿hay paper, demo, código o datos, o solo anuncio?
- **Hype** (penaliza): marketing, superlativos sin datos, ciclo de noticias.
- Instrucción explícita: valorar lo que "parece un juguete raro pero funciona" y lo que
  aparece en varias fuentes independientes.

Se incluyen en el prompt el perfil, los tags vistos en los últimos 30 días y unos pocos ejemplos
"buenos/malos" (sacados de la config, opcional) como calibración.

### Robustez
- Rate limit: se detecta por el texto de error/`is_error` del JSON de `claude -p`
  (`rate limit`, `usage limit`, `429`, `overloaded`).
- Si la salida no es JSON válido → 1 reintento con "devuelve solo JSON"; si falla, el lote se
  marca para la próxima ejecución.
- Tope `max_llm_calls_per_run`. Si se agota la cuota (error de rate limit), se guarda el estado y
  se termina **sin error** (lo pendiente se procesa mañana).

## 7. Salida

- `public/feed.xml`: últimas `keep_entries` publicadas. Cada entrada lleva:
  - título original y enlace original;
  - descripción: `why` + puntuaciones + fuentes donde apareció;
  - categoría y tags como `<category>`.
- `public/weekly.xml`: una entrada por semana (los domingos) con las tendencias. Tags con
  crecimiento sostenido, más un análisis de Claude: "qué está emergiendo y por qué".
- `public/index.html`: página mínima con los enlaces a los feeds y la última ejecución.
- Publicación con GitHub Pages (`actions/upload-pages-artifact` + `actions/deploy-pages`).

## 8. GitHub Actions

`.github/workflows/llmrss.yml` (esquema):

```yaml
on:
  schedule: [{cron: "17 5 * * *"}]   # UTC; Actions puede retrasarlo
  workflow_dispatch:
    inputs: {dry_run: {type: boolean, default: false}}
permissions: {contents: read, actions: read, pages: write, id-token: write}
concurrency: {group: llmrss, cancel-in-progress: false}
jobs:
  run:
    runs-on: ubuntu-latest
    environment: github-pages
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - uses: actions/setup-node@v4
        with: {node-version: 22}
      - run: npm i -g @anthropic-ai/claude-code
      - uses: actions/cache/restore@v4
        id: cache
        with:
          path: state/
          key: llmrss-db-${{ github.run_id }}
          restore-keys: llmrss-db-
      - name: Run
        env:
          LLMRSS_CONFIG: ${{ vars.LLMRSS_CONFIG }}
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          LLMRSS_DRY_RUN: ${{ inputs.dry_run }}
          LLMRSS_CACHE_HIT: ${{ steps.cache.outputs.cache-matched-key != '' }}
        run: |
          uv run llmrss validate-config
          uv run llmrss run
          uv run llmrss weekly   # no-op si no han pasado 7 días
      - uses: actions/cache/save@v4
        if: always()
        with: {path: state/, key: "llmrss-db-${{ github.run_id }}"}
      - uses: actions/upload-artifact@v4      # copia de seguridad de la BD
        if: always()
        with: {name: llmrss-db, path: state/, retention-days: 30}
      - uses: actions/upload-pages-artifact@v3
        if: ${{ !inputs.dry_run }}
        with: {path: public/}
      - uses: actions/deploy-pages@v4
        if: ${{ !inputs.dry_run }}
```

### Avisos sobre la cache (importante)
- La cache de Actions **no es almacenamiento persistente**. Se borra tras 7 días sin
  accesos y por LRU si el repo pasa de 10 GB. Además, cada clave es inmutable, de ahí la clave
  por `run_id` y `restore-keys` por prefijo.
- Mitigaciones, en orden:
  1. Una ejecución diaria mantiene la cache viva.
  2. Una **copia en artifact** en cada ejecución (30 días).
  3. Si no hay BD, `llmrss run` intenta recuperar la del último artifact (API de GitHub). Si tampoco hay, **siembra** `published` desde el
     `feed.xml` ya publicado en Pages, para no duplicar ni perder el feed. Se pierden las
     tendencias, no el feed.
- Limpieza: un paso opcional borra las caches `llmrss-db-*` salvo las 3 más recientes
  (`gh cache delete`).

### Otros avisos
- **Repo público o privado**: Pages en repo privado requiere plan de pago. En repo público, el
  feed es público. Las variables y secretos no se ven, pero los **logs sí**: no imprimir perfil,
  config ni prompts.
- **60 días de inactividad**: en repos públicos, GitHub desactiva los workflows programados si
  no hay actividad en el repo. `keepalive.yml` (mensual, `actions: write`) los reactiva por API
  (`PUT .../workflows/<wf>/enable`), sin commits vacíos.
- **Token de suscripción**: `claude setup-token` genera un token de larga duración. Cuando
  caduque, la ejecución debe fallar con un mensaje claro ("renueva CLAUDE_CODE_OAUTH_TOKEN").
- **Cuota**: unas 5–15 llamadas/día con los lotes por defecto. La ejecución comparte cuota con tu
  uso interactivo de Claude.

## 9. Desarrollo local

- `LLMRSS_CONFIG="$(cat config.yaml)" uv run llmrss run --dry-run`: hace fetch y LLM, pero solo
  escribe `public/` y muestra un resumen. No toca `published`.
- `llmrss run --no-llm`: fetch y dedup, para probar fuentes sin gastar cuota.
- `llmrss rebuild-feed`: regenera `public/` desde la BD.

## 10. Tests

- Config: YAML/JSON válido o inválido, overrides `LLMRSS_*`, valores por defecto.
- `normalize`: canonicalización de URLs (utm, arXiv abs/pdf, http/https, barra final).
- Fuentes con fixtures grabados (sin red).
- `llm.py` con un `claude` falso (script en PATH) que devuelve JSON válido, inválido o rate limit.
- `render`: el feed valida como RSS 2.0, conserva entradas antiguas y respeta `keep_entries`.
- Recuperación: arranque sin BD sembrando desde un `feed.xml` de fixture.

## 11. Hitos

1. ✅ Esqueleto: config, fuentes RSS y HN, SQLite, dedup, `--no-llm`.
2. ✅ LLM: prefiltro y puntuación con validación; feed.xml; `--dry-run` en local.
3. ✅ Workflow de Actions: cache, artifact, Pages, keepalive.
4. ✅ Recuperación sin cache y limpieza de caches.
5. ✅ Tendencias semanales (`weekly.xml`).
6. Calibración: 2 semanas revisando lo descartado y lo publicado. Ajustar la rúbrica y los
   ejemplos en la config.

## 12. Ideas futuras

- Feedback: un enlace "👍/👎" en cada entrada que abra un issue prerrellenado. El workflow lee
  esos issues y los añade como ejemplos de calibración.
- Descargar y extraer el texto del artículo cuando el resumen sea pobre.
- Más fuentes: listas de correo, bioRxiv, patentes, subreddits pequeños.
- Detección de aceleración (estrellas de GitHub, menciones por semana) como señal numérica
  aparte del LLM.
