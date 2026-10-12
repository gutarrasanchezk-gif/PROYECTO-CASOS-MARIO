# Casos de Mario

Programa web en español para consultar casos, registrar actuados y organizar tareas pendientes del equipo. Cada registro solicita el nombre de quien lo realiza. Permite buscar casos, asignar tareas, indicar plazos y prioridades, marcar avances, consultar el historial y exportar listados a CSV.

El acceso es mediante un enlace abierto, sin contraseña, según la preferencia solicitada. Cualquier persona con el enlace puede consultar y modificar información, además de importar archivos. El nombre del autor es declarado por quien escribe; no acredita su identidad. Comparte el enlace teniendo en cuenta que contiene información de clientes. Se puede añadir acceso privado en una siguiente versión.

## Usar el programa

1. Abre el programa y escribe tu nombre al registrar un cambio.
2. Usa **Importar Excel** para cargar `Clientes_Mario_Portugal.xlsx`. También puedes importar con el comando indicado más abajo. La importación conserva los valores originales de las hojas para consultarlos dentro de cada caso, y no duplica la carga del mismo archivo.
3. Abre un caso para agregar un actuado, revisar su historial o crear una tarea relacionada.
4. En **Tareas**, registra también pendientes generales sin asociarlos a un caso. Indica responsable, prioridad y fecha límite cuando corresponda.

La carga del archivo proporcionado se verificó con **151 expedientes agrupados, 449 registros originales, 38 tareas (24 pendientes y 14 completadas) y 73 actuaciones**. El informe señala 10 coincidencias de identidad para revisar y 7 registros con nombres incompletos. Los identificadores incompatibles permanecen separados y todos los campos originales se conservan. Los encabezados y títulos de sección se omiten como registros; las fechas ambiguas no se convierten en plazos automáticos.

Los datos se guardan en SQLite en el servidor y se comparten entre quienes visitan la misma instalación. No se guardan solo en el navegador. El Excel y la base de datos no se incluyen en el repositorio ni en la imagen Docker; una instalación nueva empieza vacía y debe recibir el Excel desde el programa.

## Ponerlo en línea

El repositorio contiene la aplicación y su configuración de despliegue. No está publicado todavía: la dirección pública la asigna el proveedor al crear el sitio. No necesitas un dominio propio para comenzar.

### Opción gratuita: PythonAnywhere

[PythonAnywhere](https://www.pythonanywhere.com/) permite alojar una aplicación Flask con SQLite y una dirección del proveedor. La cuenta gratuita tiene límites de recursos y requiere mantener vigente el sitio desde su panel. Revisa sus condiciones actuales antes de elegir esta opción.

1. Crea una cuenta en PythonAnywhere. En **Files**, sube el ZIP de la aplicación sin Excel, bases de datos ni secretos. Si el repositorio es público, también puedes clonarlo desde una consola Bash. Para un repositorio privado, usa el ZIP o la conexión segura ofrecida por el proveedor; no copies credenciales en el código.
2. En una consola Bash, descomprime el ZIP y prepara el entorno. Sustituye `TU_USUARIO` por tu usuario. Elige una versión de Python compatible disponible en tu cuenta, de 3.10 o superior, y usa la misma al crear el sitio. Este ejemplo usa 3.12:

   ```bash
   cd /home/TU_USUARIO
   unzip casos-mario-app.zip -d proyecto-casos-mario
   cd proyecto-casos-mario
   python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

3. En **Web**, agrega un sitio con **Manual configuration** y la misma versión de Python. Configura la ruta del entorno virtual como `/home/TU_USUARIO/proyecto-casos-mario/.venv`.
4. Edita el archivo WSGI que indica el panel **Web** con lo siguiente, sustituyendo `TU_USUARIO`:

   ```python
   import os
   import sys

   project = "/home/TU_USUARIO/proyecto-casos-mario"
   sys.path.insert(0, project)
   os.environ["DATABASE"] = project + "/instance/cases.sqlite3"
   os.environ["COOKIE_SECURE"] = "true"

   from app import app as application
   ```

5. Activa HTTPS si el panel ofrece esa opción y recarga el sitio con **Reload**. Abre la dirección HTTPS que muestra PythonAnywhere y utiliza **Importar Excel** para cargar los casos. Comparte esa dirección con el equipo.

El directorio `instance` conserva la base entre recargas. No lo borres al actualizar el programa. Consulta las instrucciones oficiales de [Flask en PythonAnywhere](https://help.pythonanywhere.com/pages/Flask/).

### Opción con configuración preparada: Render

`render.yaml` configura un servicio Python con Gunicorn, HTTPS, comprobación de salud y un disco persistente de 1 GB en `/var/data`. **El plan `starter` y el disco son de pago y generan un costo recurrente**; comprueba el importe vigente en Render antes de crear el servicio. El disco es necesario para conservar SQLite entre reinicios y despliegues.

1. Guarda esta versión del proyecto en el repositorio de GitHub que vas a conectar.
2. En [Render](https://render.com/), crea una cuenta y usa **New → Blueprint** para conectar `gutarrasanchezk-gif/PROYECTO-CASOS-MARIO` en la rama `main`.
3. Revisa el servicio, el disco y el costo que propone el panel a partir de `render.yaml`, y crea el servicio cuando estés conforme.
4. Cuando el despliegue termine, abre la dirección HTTPS que asignó Render y usa **Importar Excel** para cargar `Clientes_Mario_Portugal.xlsx`.
5. Comprueba que `/api/health` responde con `{"status":"ok"}`, registra una tarea de prueba y comprueba que aparece desde otro navegador.

La base se guarda en `/var/data/cases.sqlite3`. El servicio usa una sola instancia y un proceso Gunicorn con cuatro hilos, adecuado para SQLite y un equipo pequeño. Las actualizaciones se despliegan manualmente desde Render; conservan el disco. No elimines el servicio ni el disco sin descargar antes un respaldo. Documentación oficial: [Blueprints](https://render.com/docs/infrastructure-as-code), [discos persistentes](https://render.com/docs/disks) y [Flask](https://render.com/docs/deploy-flask).

## Ejecutar localmente

Requiere Python 3.10 o superior; el entorno preparado y los despliegues Render/Docker usan Python 3.12.14.

```bash
cd /workspace/PROYECTO-CASOS-MARIO
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Carga el Excel desde el navegador cuando esté abierto el programa, o importa desde la terminal indicando su ruta real:

```bash
.venv/bin/python importer.py /ruta/Clientes_Mario_Portugal.xlsx --database instance/cases.sqlite3
```

Inicia el servidor y abre `http://localhost:8000`:

```bash
.venv/bin/gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 120 --graceful-timeout 30 --access-logfile - --error-logfile - app:app
```

En desarrollo, el servidor puede reiniciarse automáticamente con Flask:

```bash
.venv/bin/flask --app app run --debug
```

El servidor de desarrollo de Flask se usa solo en la máquina local. En producción, usa Gunicorn o el servidor WSGI del proveedor.

### Docker

```bash
docker build -t casos-mario .
docker volume create casos-mario-data
docker run --name casos-mario --restart unless-stopped -p 8000:8000 -v casos-mario-data:/app/instance casos-mario
```

Abre `http://localhost:8000` e importa el Excel desde la interfaz. El volumen conserva la base entre reinicios del contenedor. El proceso ejecuta con el usuario `app`, UID 10001; si usas un directorio del host en lugar del volumen, debe ser escribible por ese usuario. Para publicarlo, configura HTTPS en un proxy y las variables de cookies y proxy indicadas abajo.

Si tu red usa un proxy HTTPS con una autoridad de confianza propia, Docker admite su certificado durante la instalación con `--secret id=pip_cert,src=/ruta/al/paquete-de-certificados-confiable.pem`. Configura también los argumentos de proxy de Docker según tu red. El certificado se monta temporalmente durante la construcción y no se incorpora a la imagen; la verificación TLS permanece activada.

### Configuración

| Variable | Uso |
| --- | --- |
| `DATABASE` | Ruta de SQLite. Por defecto, `instance/cases.sqlite3`; en Render, `/var/data/cases.sqlite3`. |
| `COOKIE_SECURE` | `true` en un sitio servido con HTTPS. Déjala desactivada para HTTP local. |
| `TRUST_PROXY` | `true` cuando el servicio está detrás del proxy de Render. No actives esta opción en un servidor expuesto directamente. |
| `SECRET_KEY` | Clave opcional de sesiones. Si no se proporciona, la aplicación genera y conserva una clave junto a la base; no hace falta escribirla en el repositorio. |
| `PORT` | Puerto de Gunicorn en la configuración de despliegue; Render lo proporciona. |

## Respaldos

Los CSV ayudan a consultar listados; el respaldo SQLite incluye también actuados, fuentes e historial. El script usa la API de respaldo de SQLite y puede ejecutarse mientras la aplicación está en uso. No copies solamente el archivo de una base activa: puede tener cambios en archivos WAL.

Ejemplo local, eligiendo un nombre nuevo para cada respaldo:

```bash
.venv/bin/python scripts/backup.py instance/cases.sqlite3 /ruta/segura/casos-mario-respaldo.sqlite3
```

En la terminal del servicio Render:

```bash
python scripts/backup.py /var/data/cases.sqlite3 /var/data/backups/casos-mario-respaldo.sqlite3
```

En PythonAnywhere:

```bash
/home/TU_USUARIO/proyecto-casos-mario/.venv/bin/python /home/TU_USUARIO/proyecto-casos-mario/scripts/backup.py /home/TU_USUARIO/proyecto-casos-mario/instance/cases.sqlite3 /home/TU_USUARIO/casos-mario-respaldo.sqlite3
```

Descarga el respaldo por los medios seguros del proveedor y guárdalo fuera del servicio. Un respaldo que permanece en el mismo disco no protege frente a la eliminación de ese disco. El script rechaza una base inexistente o vacía y no sobrescribe respaldos anteriores.

Para restaurar, detén el servicio y usa la ruta del respaldo como `DATABASE`, o sustituye la base con una copia verificada mientras la aplicación está detenida. Conserva los archivos originales hasta comprobar los casos y actuados de la restauración.

## Validación

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Para comprobar un servidor iniciado localmente:

```bash
curl --fail http://localhost:8000/api/health
```

Las pruebas se ejecutan con datos sintéticos. No subas el Excel original, `instance`, bases, respaldos ni claves al repositorio.
