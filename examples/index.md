# Acme: módulos

Generado por `module_diagrams.py` a partir de 3 mapas. No editar a mano.

## Dependencias entre módulos

Solo dependencias declaradas en Gradle entre los módulos de este grupo. Una flecha sin etiqueta es `implementation`. Las dependencias hacia otros módulos, los usos en el código y el detalle por clase están en el documento de cada módulo.

```mermaid
graph LR
    n0[":app"]
    n1[":core:network"]
    n2[":feature:login"]
    n0 --> n2
    n2 --> n1
```

## Módulos

Cada módulo enlaza a su documento.

| Módulo | Tipo | Nodos | Depende de (en este grupo) | Otros módulos |
|---|---|---|---|---|
| [:app](app.md) | android-application | 1 | :feature:login | 0 |
| [:core:network](core-network.md) | android-library | 1 | - | 0 |
| [:feature:login](feature-login.md) | android-library | 19 | :core:network | 1 |
