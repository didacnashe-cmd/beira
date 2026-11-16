# Beira

Embajada MCP: re-expone las tools de tu MCP local a tus contactos, cada uno
con su token, su lista blanca de tools y su carpeta. Todo cerrado por defecto.

```
Claude del contacto ──HTTPS──▶ túnel ──▶ Beira (127.0.0.1:8100) ──▶ MCP local (127.0.0.1:8000)
                                          │ token → contacto
                                          │ lista blanca de tools
                                          │ aislamiento de rutas
                                          │ timeout · rate limit · tamaño
                                          └ auditoría (SQLite)
```

## Alcance de la v0.2 (fase F1 del plan)

| Incluido | Tarea |
| --- | --- |
| Proxy del MCP local por Streamable HTTP, en puerto propio | T06 |
| Un token Bearer por contacto, revocable al instante (se guarda solo su hash) | T07 |
| Lista blanca de tools por contacto; lo no permitido ni se lista ni se puede llamar | T08 |
| Aislamiento de rutas de Obsidian por contacto (`..`, absolutas, `C:\`, `~` bloqueadas) | T09 |
| Timeout, rate limit por contacto y tamaño máximo de respuesta | T10 |
| Auditoría: contacto, tool, argumentos, duración, resultado, fecha | T11 |
| CLI: `invite`, `revoke`, `list`, `audit` (y `--purge-before`), `tools`, `grant`, `ungrant`, `path`, `rotate` | parte de T18 |

Fuera de esta versión: federación entre Beiras (F2), salvoconducto y asistente
de arranque (F3), Server Card, spec y publicación (F4), Service Tokens de
Cloudflare Access, imagen multi-arquitectura en GHCR (el VPS construye la suya).

## Uso

Todo se administra desde el VPS, dentro del contenedor:

```bash
docker exec beira python -m beira tools
```

```bash
docker exec beira python -m beira invite pau --tools list_obsidian_notes,read_obsidian_note --path Compartido/pau
```

```bash
docker exec beira python -m beira list
```

```bash
docker exec beira python -m beira audit --limit 50
```

```bash
docker exec beira python -m beira revoke pau
```

`invite` imprime el token **una sola vez** y el bloque de configuración que el
contacto pega en su cliente MCP. Volver a invitar a un contacto revocado le da
un token nuevo y sustituye sus permisos.

### Cambiar permisos sin revocar

Estas órdenes actúan sobre un contacto **activo** y conservan su token (salvo
`rotate`). Si el contacto no existe o está revocado, dan error y salen con
código 1.

```bash
docker exec beira python -m beira grant pau --tools append_to_obsidian_note
```

```bash
docker exec beira python -m beira ungrant pau --tools append_to_obsidian_note
```

```bash
docker exec beira python -m beira path pau --path Compartido/pau-2
```

```bash
docker exec beira python -m beira path pau --clear
```

```bash
docker exec beira python -m beira rotate pau
```

- `grant` / `ungrant` añaden o quitan tools de la lista blanca (`--tools a,b`).
- `path` cambia la carpeta del contacto (se valida igual que en `invite`);
  `--clear` la quita, y entonces las tools con ruta dejan de exponerse.
- `rotate` genera un token nuevo, invalida el anterior al instante y conserva
  tools y carpeta. Imprime el token y el bloque de configuración igual que
  `invite`.
- `grant` y `path` dan los mismos avisos que `invite`: tool en
  `BEIRA_DENY_TOOLS`, tool con ruta sin carpeta, carpeta que no limita una tool.

### Purgar la auditoría

```bash
docker exec beira python -m beira audit --purge-before 2026-01-01
```

Borra las filas de `audit` anteriores a esa fecha (UTC) e informa de cuántas
borró.

## Cómo decide Beira

1. **¿Quién eres?** `Authorization: Bearer <token>` en cada petición. Sin token
   válido: `401`. `/healthz` es lo único abierto.
2. **¿Puedes ver esta tool?** Solo si está en tu lista blanca, no está en
   `BEIRA_DENY_TOOLS` y, si usa rutas, tienes carpeta asignada.
3. **¿A qué ruta?** El argumento de ruta se reescribe para quedar dentro de tu
   carpeta: `a.md` → `Compartido/pau/a.md`. Cualquier intento de salir se
   deniega sin llegar al MCP local.
4. **Límites**, llamada al MCP local, recorte de la respuesta y **auditoría**.

`ping` es la única tool propia de Beira y la ve cualquier contacto.

## Configuración

| Variable | Por defecto | Qué es |
| --- | --- | --- |
| `BEIRA_HOST` / `BEIRA_PORT` | `127.0.0.1` / `8100` | Dónde escucha |
| `BEIRA_DB` | `/data/beira.db` en Docker | SQLite con contactos y auditoría |
| `BEIRA_UPSTREAM_URL` | `http://127.0.0.1:8000/mcp` | MCP local que se re-expone |
| `BEIRA_UPSTREAM_TOKEN` | vacío | Bearer para el MCP local, si lo pide |
| `BEIRA_PUBLIC_URL` | vacío | URL pública, para imprimirla en las invitaciones |
| `BEIRA_TIMEOUT` / `BEIRA_CONNECT_TIMEOUT` | `30` / `5` | Segundos por llamada / para conectar |
| `BEIRA_RATE_LIMIT` | `30` | Llamadas por contacto y minuto (`0` = sin límite) |
| `BEIRA_MAX_RESPONSE_BYTES` | `200000` | Tamaño máximo de respuesta |
| `BEIRA_PATH_ARGS` | tools de Obsidian | `tool=argumento` de las tools que reciben rutas |
| `BEIRA_DENY_TOOLS` | `run_bash_command` | Tools que no se exponen nunca |

## Límites conocidos

- El aislamiento de rutas es textual: un enlace simbólico dentro de la carpeta
  compartida que apunte fuera no se detecta.
- Una tool sin entrada en `BEIRA_PATH_ARGS` recibe sus argumentos tal cual
  (p. ej. `query_personal_db` da acceso a toda la base): concédela a conciencia.
- El rate limit vive en memoria: se reinicia con el contenedor.

## Desarrollo

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

Los tests de `tests/test_e2e.py` levantan un MCP local falso y un Beira real, y
los atacan con el cliente MCP oficial.

## Despliegue

Cada push a `main` ejecuta los tests, construye la imagen y, solo si todo pasa,
despliega en el VPS. Los datos viven en el volumen `beira-data`. Pasos manuales
la primera vez: añadir el hostname en el túnel apuntando a `127.0.0.1:8100` y,
si quieres, un `.env` junto al `docker-compose.yml` con `BEIRA_PUBLIC_URL`.
