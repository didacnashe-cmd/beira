# Seguridad

Beira decide quién puede llamar a qué tools de tu MCP local y sobre qué
carpetas. Un fallo aquí puede abrir tus datos a quien no debe, así que
agradecemos cualquier aviso.

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usa el aviso privado de GitHub:

1. Ve a la pestaña **Security** del repositorio.
2. Pulsa **Report a vulnerability** (GitHub Security Advisories).
3. Describe el problema, la versión afectada y, si puedes, cómo reproducirlo.

El aviso solo lo ven los mantenedores. Intentaremos responder en una semana,
acordar contigo una fecha de publicación y darte crédito si lo deseas.

## Versiones con parches

| Versión | Recibe parches de seguridad |
| --- | --- |
| 0.2.x (la última publicada) | Sí |
| < 0.2 | No |

Mientras Beira esté en 0.x solo se corrige la última versión menor. Los
parches salen como versión nueva; actualiza en cuanto se publiquen.

## Qué entra en el alcance

- **Autenticación**: acceso sin token válido, tokens que sobreviven a
  `revoke`, fugas del token o de su hash, rutas abiertas además de `/healthz`.
- **Aislamiento de rutas**: cualquier forma de leer o escribir fuera de la
  carpeta asignada al contacto (`..`, rutas absolutas, `C:\`, `~`, codificaciones
  raras…), salvo los límites conocidos de abajo.
- **Lista blanca**: ver o llamar una tool que no está en la lista del contacto
  o que está en `BEIRA_DENY_TOOLS`.
- **Límites**: saltarse el timeout, el rate limit o el tamaño máximo de
  respuesta, o tumbar Beira con una sola petición.
- **Auditoría**: llamadas que no quedan registradas o registros falseables.

Fuera del alcance: fallos del MCP local que Beira re-expone, del túnel o proxy
que pongas delante, y configuraciones que el README desaconseja (por ejemplo,
exponer Beira sin TLS; ver [docs/exponer-beira.md](docs/exponer-beira.md)).

## Límites conocidos

Ya están documentados en el [README](README.md#límites-conocidos); no hace
falta reportarlos, aunque las ideas para cerrarlos son bienvenidas:

- El aislamiento de rutas es textual: un enlace simbólico dentro de la carpeta
  compartida que apunte fuera no se detecta.
- Una tool sin entrada en `BEIRA_PATH_ARGS` recibe sus argumentos tal cual
  (p. ej. `query_personal_db` da acceso a toda la base): concédela a conciencia.
- El rate limit vive en memoria: se reinicia con el contenedor.
