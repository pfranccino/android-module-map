# :feature:login: arquitectura

Generado por `module_diagrams.py` a partir del mapa del 2026-10-02T23:39:02+00:00. 19 nodos y 42 aristas. No editar a mano: se regenera desde el mapa.

## Arquitectura por capas

Flecha sólida: depende de o llama a. Flecha punteada: relación de tipos.

```mermaid
graph TD
    subgraph Presentation
        n2["LoginActivity"]
        n3["LoginScreen"]
        n4["LoginForm"]
        n5["LoginViewModel"]
    end
    subgraph Domain
        n0["AuthRepository"]
        n6["LoginUseCase"]
    end
    subgraph Data
        n7["AuthApi"]
        n1["AuthRepositoryImpl"]
        n8["SessionStore"]
    end
    subgraph DI
        n9["LoginModule"]
    end
    subgraph ext_modules["Otros módulos"]
        n10["ApiClient<br/>:core:network"]
    end
    n1 --> n10
    n1 --> n7
    n1 --> n8
    n2 --> n3
    n3 --> n4
    n3 --> n5
    n5 --> n6
    n6 --> n0
    n9 --> n10
    n0 -.implementado por.-> n1
```

## Flujos desde los ViewModels

La etiqueta de cada flecha que sale de un ViewModel es la función que inicia la llamada. Cada implementación se dibuja como su interfaz. Solo llamadas dentro del módulo que el AST confirmó por el tipo del receptor o que codegraph resolvió con confianza de 0.9 o más.

```mermaid
graph LR
    n0["LoginViewModel"]
    n1["LoginUseCase"]
    n2["AuthRepository"]
    n3["AuthApi"]
    n4["SessionStore"]
    n0 -->|"submit"| n1
    n1 --> n2
    n2 --> n3
    n2 --> n4
```

## Secuencia: LoginViewModel.submit

```mermaid
sequenceDiagram
    participant n0 as LoginViewModel
    participant n1 as LoginUseCase
    participant n2 as AuthRepository
    participant n3 as AuthRepositoryImpl
    participant n4 as AuthApi
    participant n5 as toSession
    participant n6 as SessionStore
    Note over n0: submit()
    n0->>n1: invoke()
    n1->>n2: login()
    n2-->>n3: implementado por
    n3->>n4: login()
    n3->>n5: toSession()
    n3->>n6: save()
```

Solo llamadas entre clases, en orden de línea. No refleja ramas, bucles ni asincronía; otro flujo con `--flow Clase.funcion`.

## Dependencias entre módulos

Salientes: declaradas en Gradle. Entrantes (`usa`): usos observados en el código.

```mermaid
graph LR
    n0[":feature:login"]
    n1[":app"] -->|usa| n0
    n0 -->|api| n2[":core:model"]
    n0 -->|implementation| n3[":core:network"]
```

## Inyección de dependencias

```mermaid
graph LR
    n0["LoginModule"]
    n1["AuthRepository"]
    n2["AuthApi"]
    n3["AuthRepositoryImpl"]
    n4["SessionStore"]
    n5["LoginUseCase"]
    n6["LoginViewModel<br/>@HiltViewModel"]
    subgraph outside["Provisto fuera de este módulo"]
        n7["ApiClient"]
    end
    n0 -->|"@Binds"| n1
    n1 -.implementado por.-> n3
    n0 -->|"@Provides"| n2
    n3 -->|inyecta| n2
    n3 -->|inyecta| n7
    n3 -->|inyecta| n4
    n5 -->|inyecta| n1
    n6 -->|inyecta| n5
```

## Clases

| Clase | Tipo | Capa | Rol | Responsabilidad (KDoc) |
|---|---|---|---|---|
| LoginActivity | class | Presentation | activity | (sin KDoc) |
| LoginScreen | function | Presentation | composable | (sin KDoc) |
| LoginForm | function | Presentation | composable | (sin KDoc) |
| LoginViewModel | class | Presentation | viewmodel | Coordina el flujo de inicio de sesión y expone el estado a la UI. |
| LoginUiState | sealed interface | Presentation | - | (sin KDoc) |
| LoginUiState.Idle | data object | Presentation | - | (sin KDoc) |
| LoginUiState.Loading | data object | Presentation | - | (sin KDoc) |
| LoginUiState.Success | data class | Presentation | - | (sin KDoc) |
| LoginUiState.Error | data class | Presentation | - | (sin KDoc) |
| Session | data class | Domain | - | (sin KDoc) |
| AuthRepository | interface | Domain | repository | (sin KDoc) |
| LoginUseCase | class | Domain | usecase | (sin KDoc) |
| AuthApi | interface | Data | api_service | (sin KDoc) |
| LoginRequest | data class | Data | - | (sin KDoc) |
| LoginResponse | data class | Data | - | (sin KDoc) |
| AuthRepositoryImpl | class | Data | repository | (sin KDoc) |
| SessionStore | class | Data | - | (sin KDoc) |
| LoginModule | abstract class | DI | di_module | (sin KDoc) |

## Violaciones de capas

No se encontraron violaciones. Se revisaron todas las aristas entre capas con estas reglas: Presentation no depende de Data, Domain no depende de Presentation ni de Data, Data no depende de Presentation. La capa DI está exenta.

## Puntos de entrada

**Componentes del Manifest**

| Tipo | Clase | Exported |
|---|---|---|
| activity | com.acme.login.ui.LoginActivity | sí |

**Usado desde otros módulos**

| Elemento de este módulo | Lo usa | Módulo | Tipo |
|---|---|---|---|
| LoginScreen | MainNav | :app | calls |

## Dependencias externas

| Origen | Tipo externo | Usado por |
|---|---|---|
| :core:network | com.acme.network.ApiClient | AuthRepositoryImpl, LoginModule |
| librería | android.os.Bundle | LoginActivity |
| librería | androidx.activity.ComponentActivity | LoginActivity |
| librería | androidx.lifecycle.ViewModel | LoginViewModel |
| librería | kotlinx.coroutines.flow.StateFlow | LoginViewModel |

Declarados en Gradle sin uso observado en el código Kotlin (el mapa no ve recursos ni código generado, así que no implica que sobren): :core:model.

## Aristas a revisar

- Confianza 0.7: `AuthRepositoryImpl → toSession` (calls: login → toSession, feature/login/src/main/kotlin/com/acme/login/data/AuthRepositoryImpl.kt:26). Resuelta por nombre; revisar en el código.
- Corregida: `LoginViewModel → LoginUseCase` (submit → invoke, feature/login/src/main/kotlin/com/acme/login/ui/LoginViewModel.kt:28). codegraph proponía `AuthApi`; se usó el tipo declarado del receptor.
