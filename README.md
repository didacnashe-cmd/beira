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
| CLI mínima: `invite`, `revoke`, `list`, `audit`, `tools` | parte de T18 |

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

## Cómo decide Beira

1. **¿Quién eres?** `Authorization: Bearer <token>` en cada petición. Sin token
   válido: `401`. `/healthz` es lo único abierto. Una IP que acumula
   `BEIRA_AUTH_FAIL_LIMIT` respuestas `401` en un minuto recibe `429` con
   `Retry-After` durante un minuto, sin que Beira consulte SQLite.
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
| `BEIRA_AUTH_FAIL_LIMIT` | `20` | `401` por IP y minuto antes de bloquearla 60 s con `429` (`0` = sin freno) |
| `BEIRA_TRUST_PROXY_HEADER` | vacío | Cabecera con la IP real del cliente; detrás del túnel de Cloudflare, `CF-Connecting-IP`. Vacío = IP del socket |
| `BEIRA_UPSTREAM_BACKOFF` | `10` | Segundos que, tras un fallo de conexión, se da por caído el MCP local y se falla al instante (`0` = desactivado) |

## Límites conocidos

- El aislamiento de rutas es textual: un enlace simbólico dentro de la carpeta
  compartida que apunte fuera no se detecta.
- Una tool sin entrada en `BEIRA_PATH_ARGS` recibe sus argumentos tal cual
  (p. ej. `query_personal_db` da acceso a toda la base): concédela a conciencia.
- El rate limit, el freno a tokens inválidos y la caché de «MCP local caído»
  viven en memoria: se reinician con el contenedor.
- Detrás del túnel, sin `BEIRA_TRUST_PROXY_HEADER` todas las peticiones llegan
  desde la IP de `cloudflared`, así que una ráfaga de tokens malos bloquearía a
  todos. Configúrala solo si Beira no es accesible salvo a través del túnel
  (escucha en `127.0.0.1`): quien llegue directo podría falsear la cabecera.

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
