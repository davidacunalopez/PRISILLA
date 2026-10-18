# BITACORA — Transportes

#

CRM de Prisilla. Sistema local para una flota pequeña en Costa Rica: vencimientos de seguros, bitácora de viajes (para la contadora) y gasto de gasolina. Corre en una sola máquina, un solo usuario. Los datos viven en `data/basedatos.xlsx`. El nombre visible y los datos generales pueden cambiarse desde **Configuración**.

## Cómo abrir la app

1. Instale [Python 3.11+](https://www.python.org/downloads/) y marque **Add python.exe to PATH**.
2. Doble clic en `iniciar_app.bat`.
3. La primera vez crea el entorno virtual, instala dependencias y genera el libro Excel vacío.
4. Se abre una ventana de Windows (pywebview). Si eso falla en su máquina, se abre el navegador en `http://127.0.0.1:8765`.

No abra `basedatos.xlsx` en Excel mientras la app está en uso: el archivo queda bloqueado y no se podrá guardar. Para ver o enviar datos, use **Exportar** en Viajes.

## Módulos

| Módulo | Para qué |
| --- | --- |
| Tablero | Semáforo de pólizas (rojo ≤ 7 días o vencido, amarillo 8–28 días, verde > 28). |
| Camiones | Alta de la flota y asignación opcional de un chofer habitual. |
| Seguros | Pólizas Auto / Carga / Riesgos Laborales, mensuales o trimestrales. La fecha límite de pago se sincroniza con la fecha fin; **Renovar** cierra el período y crea el siguiente. |
| Estaciones y choferes | Catálogos operativos para la bitácora; se archivan sin perder el historial. |
| Viajes | Registro por fecha con semana ISO automática, categoría, código de ruta, empresa de trabajo, tarifa/moneda, camión/chofer y exportación Excel/CSV. Incluye columnas configurables, control booleano de envío y generación agrupada del mensaje para la contadora. |
| Gasolina | Facturas por tipo de combustible, vehículo y chofer; precio por litro automático, filtros por período, comparación mensual y gráficos con participación del gasto. |
| Pregúntale a la IA | Asistente Gemini: analiza viajes, gasolina, seguros y flota, y ayuda con rutas y estaciones. Puede proponer cambios, pero nada se guarda sin su confirmación. |
| Configuración | Nombre de la aplicación/empresa, moneda, rutas con código y tarifa vigente modificable, umbrales, cantidad de respaldos e importación segura. Los cambios de tarifa actualizan viajes pendientes; los enviados conservan su precio. |
| Inventario | Reservado; no se construye ahora. |

Los umbrales del semáforo están en `config.json`. Los días restantes **no** se guardan en Excel: se calculan al abrir.

`config.json` es local a cada máquina y no se versiona (la app lo reescribe con rutas, empresas y marcas). Si no existe, la app arranca con valores por defecto y lo crea al guardar la primera configuración.

Cada escritura hace una copia con fecha y hora en `data/backups/` y conserva las últimas 10 (configurable en `backups_conservar`).

## Rutas

Una ruta es un **par ordenado** estación de salida → estación de llegada, con su código, precio vigente y moneda (CRC o USD). En **Configuración → Códigos de rutas**:

- Varias rutas pueden **llegar a la misma estación** (A → C y B → C) o **salir de la misma** (A → B y A → C). Lo único que no se repite es el mismo par exacto: si se guarda otra vez, se actualiza esa ruta (y los viajes pendientes que la usan).
- **El sentido importa**: A → B y B → A son rutas distintas y se registran por separado.
- Varias rutas pueden **compartir el mismo código**; el sistema lo permite, pero lo marca con la etiqueta «Código compartido» para revisarlo.
- Al elegir salida y llegada, debajo aparece si la ruta es **nueva** o **ya existe** (con su código y precio), y qué otras rutas llegan a esa llegada, salen de esa salida o hacen el recorrido de regreso.
- La tabla permite **buscar** (por estación o código, sin importar tildes), **ordenar** por salida, llegada o código, y filtrar **solo códigos compartidos**. Si una ruta tiene un problema (estación archivada, precio faltante) se indica debajo de la llegada.

## Trabajo en dos computadoras (desarrollo y operación)

El repositorio solo lleva el **código**. Lo que la app escribe es propio de cada computadora y `git pull` nunca lo toca ni lo sube:

| Se queda local (está en `.gitignore`) | Contiene |
| --- | --- |
| `data/basedatos.xlsx`, `data/backups/`, `data/exportaciones/`, `data/ia_cambios.jsonl` | viajes, gasolina, seguros, flota, sus respaldos y la bitácora de cambios de la IA |
| `config.json` | nombre, umbrales, marcas, empresas, **rutas y tarifas** |
| `.env` | claves de Gemini y correo |
| `.venv/` | entorno de Python (lo crea `iniciar_app.bat`) |

Para actualizar la computadora de Priscilla, abrir una terminal en la carpeta del proyecto y ejecutar `git pull`. Una sola vez conviene dejar `git config pull.ff only`, así un `pull` nunca crea mezclas inesperadas.

Como nada de eso se versiona, lo que cada computadora registre (rutas, tarifas, viajes) **nunca genera conflictos** al hacer `git pull`; tampoco se mezcla con lo de la otra.

**Solo si `git pull` se queja de `config.json`** (hasta hace poco el archivo estaba versionado), no perderá nada si sigue estos pasos:

```
copy config.json ..\config_respaldo.json
git checkout -- config.json
git pull
copy ..\config_respaldo.json config.json
```

Si el `pull` ya terminó sin avisos, no hay nada que hacer.

## Cómo se almacenan los datos

- `data/basedatos.xlsx` es el libro principal. Cada entidad tiene su propia hoja y tabla: camiones, seguros, estaciones (hoja interna `Empresas` por compatibilidad), choferes, viajes y gasolina.
- La aplicación es la única que debe escribir en ese libro. Cada alta, edición, archivo o eliminación crea primero una copia completa en `data/backups/`.
- Los respaldos tienen nombres con fecha y hora. La cantidad conservada se define en **Configuración**; al superar ese número se eliminan los más antiguos.
- El guardado se realiza primero en un archivo temporal verificado y después reemplaza el libro principal, reduciendo el riesgo de corrupción si una escritura falla.

Excel admite hasta 1.048.576 filas por hoja, pero la aplicación se volverá lenta mucho antes porque `openpyxl` carga y guarda el libro completo en cada escritura. Para esta flota, varios miles de viajes y facturas siguen siendo un volumen razonable. Conviene revisar una migración o archivado anual cuando el libro se acerque a 25–50 MB o a decenas de miles de registros. Como referencia, 10 respaldos de un libro de 50 MB ocuparían aproximadamente 500 MB.

## Exportaciones e importación de respaldos

- **Exportar Excel/CSV** en Viajes guarda el resultado filtrado en `data/exportaciones/`. La pantalla confirma el nombre generado y permite abrir esa carpeta. Este flujo funciona tanto en la ventana de escritorio como en el navegador.
- **Generar mensaje** usa los viajes pendientes del filtro actual, los agrupa por empresa y código, prepara un texto listo para copiar y los marca como enviados. La opción **Con IA** mejora únicamente la redacción; BITACORA rechaza cualquier respuesta que altere las cifras calculadas.
- El filtro **Precio** de la bitácora busca la tarifa exacta; los filtros **Desde** y **Hasta** continúan funcionando como rango de fechas.
- La casilla **Enviada** congela la tarifa del viaje. Si se desmarca, el viaje vuelve a pendiente y recupera la tarifa vigente de la ruta. El selector **Columnas** permite mostrar u ocultar los datos secundarios y recuerda la preferencia en ese equipo.
- **Importar respaldo** está en Configuración. Solo acepta libros `.xlsx` válidos con la estructura de BITACORA, los migra si pertenecen a una versión anterior compatible y, antes de reemplazar los datos actuales, crea automáticamente otro respaldo de seguridad.
- La importación reemplaza el libro activo completo. La pantalla muestra una advertencia y solicita confirmación explícita porque los registros posteriores al respaldo dejarán de aparecer.

## Pregúntale a la IA

1. Cree una API key en Google AI Studio.
2. Abra **Configuración → Gemini · Pregúntale a la IA**.
3. Pegue la clave, seleccione **Habilitar** y guarde.
4. Use **Probar conexión** para verificar API key y modelo sin enviar datos operativos.
5. Abra **Pregúntale a la IA** desde el menú lateral.

La clave se guarda en `.env`, que está excluido del repositorio, y nunca se vuelve a mostrar en la interfaz. BITACORA calcula localmente los resúmenes y envía a Gemini únicamente la información necesaria para responder. No envía el libro completo ni teléfonos, correos, licencias, contactos, contenedores, chasis, guías o manifiestos. Gemini nunca escribe por su cuenta: solo puede proponer cambios que usted confirma (ver «Cambios con confirmación»).

**Ayuda con rutas.** Además de analizar datos, sirve para preguntar «¿tenemos una ruta de A a B?», «¿por qué no me deja guardar un viaje en esta ruta?» o «¿cómo registro la ruta de A a B?». BITACORA revisa localmente las rutas (si existe cada sentido, códigos compartidos, estaciones archivadas, precio faltante, viajes en rutas sin configurar) y le entrega ese diagnóstico a Gemini junto con las reglas de la app. Debajo de la respuesta aparece un botón que abre Configuración con la salida y la llegada ya elegidas. En cada pregunta se envían, además del resumen operativo, el nombre y estado de las estaciones y las rutas con su código y tarifa.

**Cambios con confirmación.** Si usted lo pide («registra la ruta de A a B, código 51, precio 5000»), la IA puede *proponer* crear o actualizar rutas, eliminarlas, y crear, archivar o reactivar estaciones. Nada se guarda por sí solo: cada propuesta aparece en una tarjeta con el detalle exacto (por ejemplo `Código: 35145 → 35146`, o cuántos viajes pendientes cambian de precio) y solo se aplica al pulsar **Confirmar y guardar** o **Confirmar todos en orden**. Reglas de seguridad:

- Cada propuesta se valida igual que los formularios, y otra vez al confirmar (por si algo cambió mientras tanto).
- Cada propuesta se usa una sola vez y vence a los 30 minutos. Las eliminaciones piden una segunda confirmación. Máximo 5 cambios por respuesta.
- Si una ruta necesita una estación nueva, se propone primero la estación; confirmar en otro orden no pierde la propuesta.
- Cada cambio aplicado queda en `data/ia_cambios.jsonl`, con el estado anterior para poder reconstruirlo.
- Por ahora quedan fuera viajes, gasolina, camiones, choferes y seguros: se siguen registrando a mano.

Las consultas requieren Internet y están sujetas a los precios, límites y condiciones del proyecto de Gemini utilizado. Para datos operativos reales se recomienda un proyecto con facturación activa por sus condiciones de tratamiento de datos.

También puede restaurar un respaldo desde **Configuración → Importar respaldo**. Como alternativa administrativa, cierre la aplicación y ejecute:

```
.venv\Scripts\python.exe scripts\restaurar_backup.py data\backups\NOMBRE_DEL_RESPALDO.xlsx
```

El comando valida el respaldo y conserva una copia adicional del libro reemplazado.

## Correo diario de vencimientos

El script `scripts/revisar_vencimientos.py` lee el Excel y envía un correo diario únicamente si hay pólizas rojas (7 días o menos, incluidas las vencidas).

### 1. Credenciales (Gmail u otro SMTP)

1. Copie `.env.example` a `.env` (ese archivo no se comparte).
2. Si usa Gmail:
   - Active la verificación en 2 pasos en la cuenta de Google.
   - Cree una [contraseña de aplicación](https://myaccount.google.com/apppasswords) (no use la contraseña normal).
   - En `.env`:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=su.correo@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx
ALERT_EMAIL_TO=quien.recibe@correo.com
```

3. Pruebe a mano, desde esta carpeta:

```
.venv\Scripts\python.exe scripts\revisar_vencimientos.py
```

Si no hay pólizas en umbral, el script termina bien y no manda correo. Para forzar una prueba, ponga una póliza activa con fecha de fin cercana (o ya vencida) y vuelva a correrlo.

### 2. Tarea diaria en el Programador de tareas de Windows

1. Abra **Programador de tareas** → *Crear tarea básica*.
2. Nombre: `BITACORA — revisar vencimientos`.
3. Desencadenador: todos los días, por ejemplo a las 07:00.
4. Acción: *Iniciar un programa*.
   - Programa: la ruta completa a `.venv\Scripts\python.exe` (dentro de esta carpeta).
   - Argumentos: `scripts\revisar_vencimientos.py`
   - *Iniciar en*: la ruta completa de esta carpeta (importante, para que encuentre `data` y `.env`).
5. En *Condiciones*, desmarque “iniciar solo si hay alimentación de CA” si es una laptop.

## Notas

- Moneda: CRC. “Hacienda” = Ministerio de Hacienda de Costa Rica.
- Facturación v1 solo exporta la bitácora; el esquema deja espacio para tarifas e `ID_Factura` más adelante.
- Si ya tiene una bitácora digital en otro Excel, avise antes de cargar viajes a mano: se puede armar un importador puntual.
