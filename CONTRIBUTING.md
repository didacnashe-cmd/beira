# Contribuir a Beira

Gracias por echar una mano. Beira es pequeño a propósito: cada cambio debe
mantener el principio de **todo cerrado por defecto**.

## Entorno

Necesitas Python 3.11.

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

Los tests de `tests/test_e2e.py` levantan un MCP local falso y un Beira real, y
los atacan con el cliente MCP oficial. No hace falta Docker para ejecutarlos.

## Cómo enviar un cambio

1. Abre un issue antes si el cambio es grande o toca la seguridad o la spec
   (ver [GOVERNANCE.md](GOVERNANCE.md)). Las vulnerabilidades no van en issues:
   sigue [SECURITY.md](SECURITY.md).
2. Trabaja en una rama propia; nunca directamente sobre `main`.
3. Acompaña cada cambio de comportamiento con tests. Si arreglas un fallo,
   añade primero el test que lo reproduce.
4. Abre un pull request contra `main`. CI ejecuta los tests y comprueba que la
   imagen Docker construye y arranca; tiene que quedar en verde.
5. Si cambias variables, comandos o límites, actualiza el
   [README](README.md) en el mismo PR.

## Ojo: `main` se despliega solo

Cada push a `main` ejecuta los tests, construye la imagen y, si todo pasa,
despliega automáticamente. Por eso solo los mantenedores fusionan, y solo PRs
revisados y con CI en verde.

## Estilo

- Cambios pequeños y enfocados; un PR, un tema.
- Documentación, comentarios y mensajes de commit en español claro y breve.
- Nada de datos personales, tokens ni direcciones reales en código, tests o
  ejemplos.

Al contribuir aceptas que tu aportación se publique bajo la licencia del
proyecto, [Apache-2.0](LICENSE).
