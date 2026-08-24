# Sistema de administración — Transportes (camiones, facturación, gasolina)

## Contexto

Es una empresa pequeña de transporte de camiones (Costa Rica) con 8 camiones que hoy administra tres procesos en papel: control de vencimientos de seguros (Auto, Carga, Riesgos Laborales), la bitácora semanal de viajes que alimenta la facturación con la contadora, y el registro de gasto en gasolina. El proceso en papel genera riesgo real de pagar un seguro tarde y consume tiempo cada semana copiando datos a mano antes del corte del lunes al mediodía. El objetivo es un sistema local (una sola máquina, un solo usuario) que reemplace el papel con una interfaz agradable, use Excel como almacenamiento de datos (decisión explícita del usuario), y deje espacio para crecer (facturación con montos, inventario) sin rediseñar lo ya construido.

Decisiones confirmadas con el usuario:
- **Stack**: 100% Python (no requiere Node/npm). FastAPI como backend + Jinja2/HTMX/Tailwind para una interfaz moderna, envuelta en una ventana nativa con **pywebview** (así se abre como un programa normal de Windows, no como una pestaña de navegador). Si pywebview da problemas en la máquina, cae de vuelta a abrir el navegador por defecto — se deja como plan B, no bloquea el desarrollo.
- **País**: Costa Rica → moneda CRC, "Hacienda" = Ministerio de Hacienda, precio de combustible referenciado a RECOPE/ARESEP (precio por decreto, no cambia literalmente a diario).
- **Alcance de Facturación v1**: solo registrar y exportar los datos del viaje (bitácora digital), **sin** calcular montos. El esquema se deja abierto para añadir tarifas/facturas en una iteración futura sin romper lo existente.
- **Notificaciones de seguros**: dashboard con semáforo (verde >28 días, amarillo 8–28 días, rojo ≤7 días) **+** correo automático diario solo para pólizas rojas. El correo requiere una tarea programada de Windows (Task Scheduler) que corra un script independiente una vez al día, y credenciales SMTP guardadas fuera del Excel (`.env` local).

## Diseño de datos (Excel como base de datos)

Un solo libro `data/basedatos.xlsx`, con una hoja por entidad, cada una formateada como Tabla de Excel. La app es la única que escribe en el archivo; los cálculos de "días para vencer"/semáforo se hacen en vivo en la app, **no** se guardan como valores fijos en el Excel (para no tener datos obsoletos). El `schema.py` de la app es la fuente única de verdad de las columnas, usada tanto para crear el libro como para validar formularios.

**Camiones**: ID_Camion, Placa, Marca, Modelo, Anio, Capacidad, Estado (Activo/Inactivo), Notas, ID_ChoferPredeterminado (FK opcional→Choferes)

**Seguros** (una fila por póliza/período; "Renovar" crea la siguiente fila y marca la anterior): ID_Seguro, ID_Camion (FK), TipoSeguro (Auto/Carga/Riesgos Laborales), Aseguradora, NumeroPoliza, Periodicidad (Mensual/Trimestral), FechaInicio, FechaFin, MontoPrima, Moneda, Estado (Activo/Renovado/Cancelado — ciclo de vida, no el semáforo), FechaUltimoPago (mostrada como Fecha límite de pago y sincronizada con FechaFin), Notas

**Estaciones** (hoja interna `Empresas` por compatibilidad; puntos de salida o llegada): ID_Empresa, NombreEmpresa, Contacto, Telefono, Email, Notas, Estado

**Choferes** (catálogo nuevo, recomendado para evitar typos y poder filtrar por chofer): ID_Chofer, Nombre, Telefono, Licencia, Estado

**Viajes** (bitácora/facturación): ID_Viaje, Fecha, Semana ISO automática, ID_Salida/ID_Llegada (FK→Estaciones), CodigoRuta, Categoria (Viaje completo/Desvío/Ruptura), EmpresaTrabajo, Precio, Moneda (CRC/USD), ID_Camion, ID_Chofer, Contenedor, Chasis, GuiaTirManifiesto, Enviada (boolean), Estado y FechaRegistro. Código, precio y moneda provienen de la tarifa vigente configurada. Los cambios de tarifa se propagan a viajes con `Enviada=false`; al enviarlos se congela su precio. La bitácora permite configurar columnas y genera el informe agrupado por empresa y código, opcionalmente con redacción de Gemini validada para impedir cambios de cifras. `Ruptura` se asigna exclusivamente a rutas internas donde salida y llegada son la misma estación. `Desde`/`Hasta` se conservan internamente para compatibilidad histórica, pero la interfaz utiliza una fecha única.

**Gasolina**: ID_Registro, Fecha, TipoCombustible (Diesel/Super/Regular), CantidadComprada(L), ID_Camion (FK), NumeroBoleta, ID_Chofer (FK), MontoTotal, PrecioPorLitro (calculado como total/litros), Moneda

**Config fuera del Excel** (`config.json` + `.env`, nunca credenciales en el Excel): identidad general, umbrales de alerta (amarillo ≤28 días, rojo ≤7 días), datos SMTP y destinatario del correo de avisos.

## Estructura del proyecto

```
BITACORA/
  app/
    main.py                  # arranque FastAPI + ventana pywebview
    config.py                # carga config.json / .env
    db/
      schema.py              # definición de columnas por hoja (fuente única de verdad)
      plantilla_base.py      # crea basedatos.xlsx con sus hojas si no existe
      excel_repo.py          # leer/insertar/actualizar filas, generar IDs, backups
    routers/                 # camiones, seguros, empresas, choferes, viajes, gasolina, dashboard
    services/
      alertas.py             # cálculo de semáforo + envío de correo
      exportar.py             # exportar filtros a xlsx/csv
    templates/                # Jinja2 + HTMX, Tailwind vía CDN
    static/
  scripts/
    revisar_vencimientos.py  # script standalone para Task Scheduler (chequeo diario + correo)
  data/
    basedatos.xlsx           # se genera automáticamente en el primer arranque
    backups/                 # copia con timestamp antes de cada escritura
  .env.example
  config.json
  requirements.txt
  iniciar_app.bat            # doble clic: activa entorno y abre la app
  README.md
```

## Plan de fases (orden de ejecución para Cursor)

**Fase 0 — Cimientos**
Crear la estructura de carpetas, entorno virtual y `requirements.txt` (fastapi, uvicorn, openpyxl, pandas, jinja2, python-multipart, pywebview, python-dotenv). Construir `schema.py` y `plantilla_base.py` (genera el Excel con todas las hojas/encabezados si no existe) y `excel_repo.py` con las funciones genéricas de lectura/escritura + generación de backups con timestamp antes de cada guardado. Layout base en HTML con navegación entre módulos y arranque vía pywebview.
*Verificación*: correr `iniciar_app.bat`, confirmar que abre una ventana con el dashboard vacío y que `data/basedatos.xlsx` se creó con todas las hojas correctas.

**Fase 1 — Seguros (el de mayor riesgo actual)**
CRUD de Camiones. CRUD de Seguros por camión y tipo, con fechas y periodicidad. Cálculo en vivo de días restantes y semáforo según `config.json`. Vista principal (dashboard) con todos los seguros ordenados por urgencia. Acción "Renovar" que crea el siguiente período a partir de uno por vencer/vencido.
*Verificación*: cargar los 8 camiones con sus pólizas reales, confirmar que el semáforo clasifica bien, confirmar que se genera un backup al guardar.

**Fase 2 — Facturación (bitácora de viajes)**
CRUD de Empresas y Choferes. Formulario de registro de viaje con selects de Salida/Llegada/Camión/Chofer y los campos de la bitácora actual (contenedor, chasis, año, guía/tir/manifiesto, fecha). Tabla con filtros (fecha, empresa, camión, estado) y exportación del resultado filtrado a Excel/CSV con formato limpio para enviar a la contadora.
*Verificación*: registrar viajes de prueba, aplicar filtros, exportar y abrir el archivo exportado.

**Fase 3 — Gasolina**
Registro de facturas de combustible con boleta, tipo, fecha, litros, total, vehículo y chofer. El precio por litro se deriva automáticamente. Vista analítica filtrable por período, con comparación mensual, promedio por factura y participación por mes, chofer, vehículo y combustible.
*Verificación*: registrar facturas y confirmar cálculo de precio, totales y gráficos.

**Fase 4 — Notificación por correo y respaldo**
Script standalone `scripts/revisar_vencimientos.py` que lee el Excel y envía un correo si hay seguros dentro del umbral rojo configurado. Documentar en el README cómo crear una contraseña de aplicación de Gmail (o el proveedor que se use) y cómo programar la tarea diaria en Windows Task Scheduler. Rutina de limpieza de backups antiguos (conservar los últimos N).
*Verificación*: forzar una fecha de vencimiento próxima de prueba, correr el script a mano y confirmar que llega el correo.

**Fase 5 (futuro, no se construye ahora)**: módulo de Inventario — se deja únicamente el espacio reservado en el menú.

## Notas técnicas importantes

- **Bloqueo de archivo**: mientras la app corre, `basedatos.xlsx` debe tratarse como propiedad de la app. Si el usuario lo abre directamente en Excel al mismo tiempo, la app no podrá guardar — debe mostrar un error claro pidiendo cerrarlo, en vez de fallar silenciosamente. Para ver los datos en Excel real, usar la función de exportar.
- **Backups**: copia con timestamp en `data/backups/` antes de cada escritura, para poder recuperar el archivo si se corrompe.
- **Credenciales de correo**: van en `.env` local (fuera de git/Excel), nunca en texto plano dentro del proyecto compartido.
- **Migración de datos existentes**: no se mencionaron datos digitales previos que migrar; si existen (ej. una bitácora en Excel ya en uso), avisar antes de la Fase 2 para diseñar un importador puntual.
