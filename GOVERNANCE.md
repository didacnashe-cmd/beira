# Gobernanza

## Quién decide

Beira es un proyecto de **THC Labs**. Los mantenedores de THC Labs revisan y
fusionan los cambios, publican las versiones y tienen la última palabra cuando
no hay consenso. Cualquiera puede proponer, opinar y revisar.

Criterios para decidir, por orden:

1. **Seguridad**: todo cerrado por defecto; nada que abra acceso sin que el
   dueño lo conceda de forma explícita.
2. **Simplicidad**: autoalojable por una sola persona, con pocas piezas.
3. **Compatibilidad**: no romper a los contactos ya invitados sin motivo.

## Cómo cambia la spec

Por "spec" entendemos el comportamiento visible de Beira: cómo autentica, qué
tools expone, cómo reescribe rutas, los límites, la CLI y las variables de
configuración.

1. **Propuesta en un issue.** Explica el problema, la propuesta y qué cambia
   para quien ya usa Beira. Etiquétalo como propuesta.
2. **Discusión.** Al menos una semana abierta para comentarios en cambios que
   rompan compatibilidad; menos si es un añadido pequeño. Los mantenedores
   cierran la discusión aceptándola, pidiendo cambios o rechazándola con
   motivo.
3. **Implementación.** PR con tests y README actualizado, enlazado al issue.
4. **Versión.** El cambio sale en una versión nueva:
   - parche (`0.2.x`): correcciones sin cambios de comportamiento visible;
   - menor (`0.x.0`): mientras estemos en 0.x, cualquier cambio de la spec,
     incluidos los que rompen compatibilidad, anunciados en las notas de la
     versión;
   - desde 1.0, los cambios incompatibles solo en versiones mayores.

Las correcciones de seguridad pueden saltarse la discusión pública y salir
directamente como parche (ver [SECURITY.md](SECURITY.md)).
