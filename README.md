# Casos de Mario

Intranet en español para consultar casos, registrar actuados y organizar tareas pendientes del equipo. Permite buscar casos, asignar tareas, indicar plazos y prioridades, marcar avances, consultar el historial y exportar listados a CSV. Cada registro identifica automáticamente al usuario que inició sesión.

El acceso es **privado, con usuario y contraseña**. Los casos, tareas, importaciones y exportaciones requieren iniciar sesión. El enlace por sí solo no permite consultar los datos.

## Acceso y usuarios

Hay dos perfiles:

- **Administrador:** consulta y registra información, importa el Excel, exporta listados y gestiona las cuentas del equipo.
- **Editor:** consulta todos los casos y registra actuados, cambios y tareas; también puede importar y exportar. No gestiona usuarios.

El administrador puede crear cuentas, cambiar su perfil o nombre, restablecer contraseñas y desactivar el acceso. No se puede desactivar la propia cuenta ni dejar al sistema sin un administrador activo. Desactivar un usuario conserva sus registros anteriores y revoca sus sesiones. Las contraseñas se guardan como hashes dentro de la base privada.

Cada usuario puede cambiar su contraseña desde la aplicación. El cambio invalida sus otras sesiones; un restablecimiento por el administrador invalida las sesiones anteriores. Los registros nuevos toman el nombre de la cuenta autenticada, y los autores históricos permanecen intactos.

### Primer administrador

Una instalación nueva muestra la pantalla de acceso con un aviso de configuración pendiente. No hay contraseña predeterminada ni registro público. Después de instalar las dependencias, crea el primer administrador desde una terminal del servidor:

```bash
.venv/bin/flask --app app create-admin --username mario --name "Mario Portugal"
```

La terminal solicita la contraseña y su confirmación sin mostrarlas. El usuario debe tener al menos 3 caracteres y la contraseña al menos 10. Elige tú la contraseña; no la añadas al comando, al código ni a los registros. Crear la cuenta conserva los casos que ya existan en esa base. Los pasos de cada alojamiento indican cómo ejecutar este comando con su entorno y base persistente.

## Usar el programa

1. Abre el programa e inicia sesión con la cuenta que te asignó el administrador.
2. Usa **Importar Excel** para cargar `Clientes_Mario_Portugal.xlsx`. También puedes importar con el comando indicado más abajo. La importación conserva los valores originales de las hojas para consultarlos dentro de cada caso, y no duplica la carga del mismo archivo.
3. Abre un caso para agregar un actuado, revisar su historial o crear una tarea relacionada.
4. En **Tareas**, registra también pendientes generales sin asociarlos a un caso. Indica responsable, prioridad y fecha límite cuando corresponda.

La carga del archivo proporcionado se verificó con **151 expedientes agrupados, 449 registros originales, 38 tareas (24 pendientes y 14 completadas) y 73 actuaciones**. El informe señala 10 coincidencias de identidad para revisar y 7 registros con nombres incompletos. Los identificadores incompatibles permanecen separados y todos los campos originales se conservan. Los encabezados y títulos de sección se omiten como registros; las fechas ambiguas no se convierten en plazos automáticos.

Los datos se guardan en SQLite en el servidor y se comparten entre quienes visitan la misma instalación. No se guardan solo en el navegador. El Excel y la base de datos no se incluyen en el repositorio ni en la imagen Docker; una instalación nueva empieza vacía y debe recibir el Excel desde el programa.

## Ponerlo en línea

El repositorio contiene la aplicación y su configuración de despliegue. No está publicado todavía: la dirección pública la asigna el proveedor al crear el sitio. No necesitas un dominio propio para comenzar.

### Hosting con cPanel y Python/Passenger

Puedes instalar el programa en un hosting que incluya **Setup Python App**, **Application Manager con Python/Passenger** o un servicio equivalente para aplicaciones WSGI, con **Python 3.10 o superior**. Un hosting que ofrece solamente PHP no puede ejecutar esta aplicación; en ese caso, usa un proveedor con Python o un VPS con Docker.

1. Crea un subdominio para el programa y habilita su certificado HTTPS desde el panel del hosting. Configura la aplicación en la raíz de ese subdominio, con **Application URL `/`**. La interfaz usa rutas como `/api/cases`, por lo que no debes montarla en una subcarpeta como `/casos`.
2. En el administrador de archivos, sube y descomprime el ZIP de la aplicación en `/home/TU_USUARIO/proyecto-casos-mario`, sustituyendo `TU_USUARIO` por tu usuario de hosting. Esta es la **Application root**, fuera de `public_html`. No pongas el Excel, la base de datos, los respaldos ni las claves en directorios públicos.
3. En **Setup Python App** o el administrador equivalente, crea la aplicación seleccionando Python 3.10 o superior y el subdominio. Indica `proyecto-casos-mario` como raíz, `passenger_wsgi.py` como **Application startup file** y `application` como **Application entry point**. Usa el modo **Production** si el panel permite elegirlo. `passenger_wsgi.py` localiza el proyecto por su propia ruta y respeta las variables que configure el hosting.
4. Activa el entorno virtual con el comando que muestra el panel. Desde Terminal/SSH, ejecuta:

   ```bash
   cd /home/TU_USUARIO/proyecto-casos-mario
   python -m pip install -r requirements.txt
   ```

   Si no dispones de terminal y el panel ofrece **Run Pip Install**, selecciona `requirements.txt`. El proveedor debe permitir instalar estas dependencias en el entorno Python de la aplicación.
5. Configura estas variables en el panel de la aplicación:

   | Variable | Valor |
   | --- | --- |
   | `DATABASE` | `/home/TU_USUARIO/proyecto-casos-mario/instance/cases.sqlite3` |
   | `COOKIE_SECURE` | `true`, con el sitio servido mediante HTTPS |
   | `AUTH_REQUIRED` | `true` |

   El usuario que ejecuta la aplicación debe poder escribir en `instance`; el programa crea ese directorio y la base automáticamente. Deja `TRUST_PROXY` sin definir, salvo que el proveedor confirme que debes confiar en su proxy. La clave de sesiones se genera y conserva junto a la base, sin necesidad de incluirla en el código.
6. Con el entorno Python activado, crea el administrador usando la misma base configurada para Passenger:

   ```bash
   cd /home/TU_USUARIO/proyecto-casos-mario
   DATABASE=/home/TU_USUARIO/proyecto-casos-mario/instance/cases.sqlite3 flask --app app create-admin --username mario --name "Mario Portugal"
   ```

   Introduce la contraseña cuando la terminal la solicite. Las variables del panel WSGI no siempre se aplican a una terminal; por eso el comando indica explícitamente la base. Si el hosting no ofrece Terminal/SSH, el proveedor debe facilitar un medio privado para ejecutar este comando.
7. Pulsa **Restart** para iniciar la aplicación. Abre el subdominio mediante HTTPS y comprueba que `/api/health` devuelve `{"status":"ok"}`. Inicia sesión, usa **Importar Excel** para cargar `Clientes_Mario_Portugal.xlsx` y crea las cuentas del equipo. Registra una tarea de prueba y comprueba que aparece desde otro navegador después de iniciar sesión.

Los casos y actuados quedan en la base persistente del hosting. Conserva `instance` al actualizar los archivos del programa. Para respaldarlos, activa el mismo entorno Python y ejecuta, usando un nombre nuevo en cada ocasión:

```bash
python /home/TU_USUARIO/proyecto-casos-mario/scripts/backup.py /home/TU_USUARIO/proyecto-casos-mario/instance/cases.sqlite3 /home/TU_USUARIO/casos-mario-respaldo.sqlite3
```

Descarga el archivo desde el administrador de archivos y guárdalo fuera del hosting. La base y sus respaldos contienen también las cuentas del equipo; mantenlos fuera de directorios públicos.

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
   os.environ["AUTH_REQUIRED"] = "true"

   from app import app as application
   ```

5. Antes de recargar el sitio, crea el administrador desde la consola Bash:

   ```bash
   cd /home/TU_USUARIO/proyecto-casos-mario
   DATABASE=/home/TU_USUARIO/proyecto-casos-mario/instance/cases.sqlite3 .venv/bin/flask --app app create-admin --username mario --name "Mario Portugal"
   ```

   Introduce la contraseña cuando la terminal la solicite; este comando usa la misma base que el archivo WSGI.
6. Activa HTTPS si el panel ofrece esa opción y recarga el sitio con **Reload**. Abre la dirección HTTPS que muestra PythonAnywhere, inicia sesión y utiliza **Importar Excel** para cargar los casos. Crea las cuentas del equipo y comparte la dirección; cada persona necesitará su cuenta para acceder.

El directorio `instance` conserva la base entre recargas. No lo borres al actualizar el programa. Consulta las instrucciones oficiales de [Flask en PythonAnywhere](https://help.pythonanywhere.com/pages/Flask/).

### Opción con configuración preparada: Render

`render.yaml` configura un servicio Python con Gunicorn, HTTPS, comprobación de salud y un disco persistente de 1 GB en `/var/data`. **El plan `starter` y el disco son de pago y generan un costo recurrente**; comprueba el importe vigente en Render antes de crear el servicio. El disco es necesario para conservar SQLite entre reinicios y despliegues.

1. El código de la aplicación está publicado en la rama `codex/casos-mario-descarga` de `gutarrasanchezk-gif/PROYECTO-CASOS-MARIO`. Usa esa rama o una rama donde ya hayas integrado sus cambios; `main` contiene solamente el README inicial mientras no se integren.
2. En [Render](https://render.com/), crea una cuenta y usa **New → Blueprint** para conectar ese repositorio y selecciona `codex/casos-mario-descarga` o la rama que ya contenga la aplicación y `render.yaml`.
3. Revisa el servicio, el disco y el costo que propone el panel a partir de `render.yaml`. Comprueba que la rama del servicio también contiene la aplicación, y crea el servicio cuando estés conforme.
4. Cuando el despliegue termine, abre la terminal **Shell** del servicio y crea el administrador:

   ```bash
   flask --app app create-admin --username mario --name "Mario Portugal"
   ```

   La terminal utiliza `DATABASE=/var/data/cases.sqlite3`, definido en el servicio. Introduce la contraseña de forma oculta cuando se solicite. Ejecuta este paso en el servicio en funcionamiento, **después del despliegue**; no lo agregues al comando de construcción, donde el disco persistente no está disponible.
5. Abre la dirección HTTPS que asignó Render, inicia sesión y usa **Importar Excel** para cargar `Clientes_Mario_Portugal.xlsx`. Crea las cuentas del equipo.
6. Comprueba que `/api/health` responde con `{"status":"ok"}`, registra una tarea de prueba y comprueba que aparece desde otro navegador después de iniciar sesión.

La base se guarda en `/var/data/cases.sqlite3`. El servicio usa una sola instancia y un proceso Gunicorn con cuatro hilos, adecuado para SQLite y un equipo pequeño. Las actualizaciones se despliegan manualmente desde Render; conservan el disco. No elimines el servicio ni el disco sin descargar antes un respaldo. Documentación oficial: [Blueprints](https://render.com/docs/infrastructure-as-code), [discos persistentes](https://render.com/docs/disks) y [Flask](https://render.com/docs/deploy-flask).

## Ejecutar localmente

Requiere Python 3.10 o superior; el entorno preparado y los despliegues Render/Docker usan Python 3.12.14.

```bash
cd /workspace/PROYECTO-CASOS-MARIO
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Crea el primer administrador:

```bash
.venv/bin/flask --app app create-admin --username mario --name "Mario Portugal"
```

Carga el Excel desde el navegador después de iniciar sesión, o importa desde la terminal indicando su ruta real:

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
docker run -d --name casos-mario --restart unless-stopped -p 8000:8000 -v casos-mario-data:/app/instance casos-mario
docker exec -it casos-mario flask --app app create-admin --username mario --name "Mario Portugal"
```

El último comando solicita y confirma la contraseña sin mostrarla. Abre `http://localhost:8000`, inicia sesión e importa el Excel desde la interfaz. El volumen conserva la base y las cuentas entre reinicios del contenedor. El proceso ejecuta con el usuario `app`, UID 10001; si usas un directorio del host en lugar del volumen, debe ser escribible por ese usuario. Para publicarlo, configura HTTPS en un proxy y las variables de cookies y proxy indicadas abajo.

Si tu red usa un proxy HTTPS con una autoridad de confianza propia, Docker admite su certificado durante la instalación con `--secret id=pip_cert,src=/ruta/al/paquete-de-certificados-confiable.pem`. Configura también los argumentos de proxy de Docker según tu red. El certificado se monta temporalmente durante la construcción y no se incorpora a la imagen; la verificación TLS permanece activada.

### Configuración

| Variable | Uso |
| --- | --- |
| `DATABASE` | Ruta de SQLite. Por defecto, `instance/cases.sqlite3`; en Render, `/var/data/cases.sqlite3`. |
| `AUTH_REQUIRED` | `true` por defecto: exige usuarios y contraseña. El modo `false` queda para pruebas de compatibilidad; no lo uses en un alojamiento público. |
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
