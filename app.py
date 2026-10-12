"""Shared case registry. Open access is an explicit deployment choice."""

import csv
from datetime import date, datetime, timezone
import fcntl
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
from urllib.parse import urlsplit
import zipfile
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from storage import connect_db, init_db


CASE_FIELDS = {
    'name', 'a_number', 'reference', 'category', 'status', 'owner',
    'country', 'court', 'hearing_date', 'notes',
}
TASK_FIELDS = {'title', 'description', 'assignee', 'due_date', 'priority', 'status'}
ACTIVITY_FIELDS = {'title', 'description', 'type', 'occurred_on'}
CASE_STATUSES = {'activo', 'cerrado'}
TASK_STATUSES = {'pendiente', 'en_curso', 'completada'}
PRIORITIES = {'alta', 'media', 'baja'}
ACTIVITY_TYPES = {'actuacion', 'llamada', 'audiencia', 'documento', 'nota'}
TASK_ORDER = """ORDER BY CASE WHEN t.status = 'completada' THEN 1 ELSE 0 END,
    CASE WHEN t.due_date = '' THEN 1 ELSE 0 END, t.due_date,
    CASE t.priority WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,
    t.created_at DESC, t.id DESC"""
FIELD_LABELS = {
    'name': 'Nombre', 'a_number': 'A-Number', 'reference': 'Referencia',
    'category': 'Categoría', 'status': 'Estado', 'owner': 'Responsable',
    'country': 'País', 'court': 'Juzgado', 'hearing_date': 'Audiencia',
    'notes': 'Notas', 'title': 'Título', 'description': 'Descripción',
    'assignee': 'Responsable', 'due_date': 'Fecha límite', 'priority': 'Prioridad',
}


class ValidationError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {'1', 'true', 'yes'}


def _persistent_secret(directory):
    """Create once, atomically, without embedding or logging the secret."""
    path = Path(directory) / '.secret_key'
    if not path.exists():
        value = secrets.token_hex(32)
        with tempfile.NamedTemporaryFile(prefix='.secret-', dir=directory, mode='w',
                                         encoding='utf-8', delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                pass
        finally:
            temporary_path.unlink(missing_ok=True)
    value = path.read_text(encoding='utf-8').strip()
    if len(value) < 32:
        raise RuntimeError('La clave de sesión persistente no es válida.')
    return value


def _text(payload, field, default='', required=False, limit=500):
    value = payload.get(field, default)
    if not isinstance(value, str):
        raise ValidationError(f'{field}: se requiere un texto.')
    value = value.strip()
    if required and not value:
        raise ValidationError(f'{field}: este campo es obligatorio.')
    if len(value) > limit:
        raise ValidationError(f'{field}: máximo {limit} caracteres.')
    return value


def _date_value(value, field, required=False):
    if not value and not required:
        return ''
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValidationError(f'{field}: usa una fecha válida AAAA-MM-DD.')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f'{field}: usa una fecha válida AAAA-MM-DD.') from None
    return value


def _enum_value(value, field, allowed):
    if value not in allowed:
        raise ValidationError(f'{field}: valor no válido.')
    return value


def _csv_value(value):
    """Spreadsheet engines may ignore whitespace before a formula marker."""
    if value is None:
        return ''
    value = str(value)
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.environ.get('DATABASE', str(Path(app.instance_path) / 'cases.sqlite3')),
        SECRET_KEY=os.environ.get('SECRET_KEY') or None,
        SESSION_COOKIE_NAME='mario_cases_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=_env_bool('COOKIE_SECURE'),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        MAX_JSON_CONTENT_LENGTH=1024 * 1024,
        TRUST_PROXY=_env_bool('TRUST_PROXY'),
        APP_TIMEZONE='America/Lima',
    )
    if config:
        app.config.update(config)
    init_db(app.config['DATABASE'])
    if not app.config['SECRET_KEY']:
        app.config['SECRET_KEY'] = _persistent_secret(Path(app.config['DATABASE']).parent)
    if app.config['TRUST_PROXY']:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    def today():
        return datetime.now(ZoneInfo(app.config['APP_TIMEZONE'])).date().isoformat()

    def database():
        if 'db' not in g:
            g.db = connect_db(app.config['DATABASE'])
        return g.db

    @app.teardown_appcontext
    def close_database(exception=None):
        connection = g.pop('db', None)
        if connection is not None:
            connection.close()

    @app.before_request
    def protect_mutations():
        if not request.path.startswith('/api/') or request.method not in {'POST', 'PATCH', 'PUT', 'DELETE'}:
            return None
        token = session.get('csrf_token')
        provided = request.headers.get('X-CSRF-Token', '')
        if not token or not provided or not secrets.compare_digest(token.encode(), provided.encode()):
            return jsonify(error='La sesión cambió. Recarga la página antes de registrar.'), 403
        origin = request.headers.get('Origin')
        if origin:
            actual = urlsplit(request.host_url)
            try:
                supplied = urlsplit(origin)
            except ValueError:
                return jsonify(error='El origen de la solicitud no está permitido.'), 403
            if (supplied.scheme, supplied.netloc) != (actual.scheme, actual.netloc):
                return jsonify(error='El origen de la solicitud no está permitido.'), 403
        if request.path != '/api/import':
            if not request.is_json:
                return jsonify(error='Envía los datos como application/json.'), 415
            if request.content_length and request.content_length > app.config['MAX_JSON_CONTENT_LENGTH']:
                return jsonify(error='El registro supera el tamaño permitido.'), 413
        return None

    @app.after_request
    def response_headers(response):
        if request.path.startswith('/api/') or request.path == '/':
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.errorhandler(ValidationError)
    def invalid_input(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            messages = {
                400: 'La solicitud no es válida.', 404: 'No se encontró el registro.',
                405: 'Método no permitido.', 413: 'El archivo supera el límite de 10 MB.',
                415: 'Formato de datos no permitido.',
            }
            return jsonify(error=messages.get(error.code, 'No se pudo completar la solicitud.')), error.code
        return error

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.error('Fallo de base de datos: %s', type(error).__name__)
        return jsonify(error='No se pudo guardar o consultar la información. Intenta nuevamente.'), 503

    def payload(allowed, patch=False):
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValidationError('Se requiere un objeto JSON.')
        extra = set(data) - allowed - {'author'}
        if extra:
            raise ValidationError('Campos no permitidos: ' + ', '.join(sorted(extra)) + '.')
        author = _text(data, 'author', required=True, limit=150)
        if patch and not (set(data) & allowed):
            raise ValidationError('Indica al menos un campo que deseas cambiar.')
        return data, author

    def case_values(data, patch=False):
        values = {}
        fields = CASE_FIELDS & set(data) if patch else CASE_FIELDS
        for field in sorted(fields):
            default = 'activo' if field == 'status' else 'Mario' if field == 'owner' else ''
            values[field] = _text(data, field, default=default, required=field == 'name',
                                  limit=20000 if field == 'notes' else 500)
            if field == 'status':
                _enum_value(values[field], field, CASE_STATUSES)
            if field == 'hearing_date':
                _date_value(values[field], field)
        return values

    def task_values(data, patch=False):
        values = {}
        fields = TASK_FIELDS & set(data) if patch else TASK_FIELDS - {'status'}
        for field in sorted(fields):
            default = 'media' if field == 'priority' else ''
            values[field] = _text(data, field, default=default, required=field == 'title',
                                  limit=20000 if field == 'description' else 500)
            if field == 'priority':
                _enum_value(values[field], field, PRIORITIES)
            if field == 'status':
                _enum_value(values[field], field, TASK_STATUSES)
            if field == 'due_date':
                _date_value(values[field], field)
        return values

    def find_record(table, record_id):
        if record_id <= 0 or record_id > 9223372036854775807:
            raise ValidationError('El identificador debe ser un entero positivo.')
        row = database().execute(f'SELECT * FROM {table} WHERE id = ?', (record_id,)).fetchone()
        if row is None:
            from flask import abort
            abort(404)
        return dict(row)

    def insert_record(table, values):
        fields = list(values)
        query = f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})"
        return database().execute(query, [values[field] for field in fields]).lastrowid

    def update_record(table, record_id, values):
        fields = list(values)
        query = f"UPDATE {table} SET {', '.join(field + ' = ?' for field in fields)} WHERE id = ?"
        database().execute(query, [values[field] for field in fields] + [record_id])

    def audit(case_id, title, description, author, timestamp):
        insert_record('activities', {
            'case_id': case_id, 'type': 'nota', 'title': title, 'description': description,
            'occurred_on': today(), 'author': author, 'created_at': timestamp,
        })

    def change_description(old, values):
        lines = [f"{FIELD_LABELS.get(field, field)}: {old[field] or '—'} → {value or '—'}"
                 for field, value in values.items() if old[field] != value]
        return '\n'.join(lines) or 'Se guardó el registro sin cambios en sus campos.'

    def joined_task(task_id):
        return dict(database().execute('''SELECT t.*, c.name AS case_name FROM tasks t
            LEFT JOIN cases c ON c.id = t.case_id WHERE t.id = ?''', (task_id,)).fetchone())

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/robots.txt')
    def robots():
        return Response('User-agent: *\nDisallow: /\n', mimetype='text/plain')

    @app.get('/api/health')
    def health():
        database().execute('SELECT COUNT(*) FROM cases').fetchone()
        return jsonify(status='ok')

    @app.get('/api/bootstrap')
    def bootstrap():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        connection = database()
        with connection:
            connection.execute('BEGIN')
            cases = [dict(row) for row in connection.execute('''SELECT c.*,
                COALESCE(a.activity_count, 0) AS activity_count,
                COALESCE(t.task_count, 0) AS task_count FROM cases c
                LEFT JOIN (SELECT case_id, COUNT(*) activity_count FROM activities GROUP BY case_id) a
                    ON a.case_id = c.id
                LEFT JOIN (SELECT case_id, COUNT(*) task_count FROM tasks
                    WHERE status != 'completada' GROUP BY case_id) t ON t.case_id = c.id
                ORDER BY c.name COLLATE NOCASE, c.id''')]
            tasks = [dict(row) for row in connection.execute('''SELECT t.*, c.name AS case_name
                FROM tasks t LEFT JOIN cases c ON c.id = t.case_id ''' + TASK_ORDER)]
            stats = dict(connection.execute('''SELECT
                (SELECT COUNT(*) FROM cases) AS case_count,
                (SELECT COUNT(*) FROM cases WHERE status = 'activo') AS active_cases,
                (SELECT COUNT(*) FROM tasks WHERE status != 'completada') AS pending_tasks,
                (SELECT COUNT(*) FROM tasks WHERE status != 'completada'
                    AND due_date != '' AND due_date < ?) AS overdue_tasks,
                (SELECT COUNT(*) FROM activities) AS activities_count''', (today(),)).fetchone())
            latest = connection.execute('SELECT report FROM import_runs ORDER BY id DESC LIMIT 1').fetchone()
            summary = json.loads(latest['report']) if latest else None
        return jsonify(cases=cases, tasks=tasks, stats=stats,
                       csrf_token=session['csrf_token'], import_summary=summary)

    @app.get('/api/cases/<int:case_id>')
    def case_detail(case_id):
        connection = database()
        with connection:
            connection.execute('BEGIN')
            case = find_record('cases', case_id)
            case['activities'] = [dict(row) for row in connection.execute('''SELECT * FROM activities
                WHERE case_id = ? ORDER BY occurred_on DESC, created_at DESC, id DESC''', (case_id,))]
            case['tasks'] = [dict(row) for row in connection.execute('''SELECT t.*, c.name AS case_name
                FROM tasks t LEFT JOIN cases c ON c.id = t.case_id WHERE t.case_id = ? ''' + TASK_ORDER,
                                                                  (case_id,))]
            sources = []
            for row in connection.execute('SELECT * FROM sources WHERE case_id = ? ORDER BY id', (case_id,)):
                source = dict(row)
                source['data'] = json.loads(source['data'])
                sources.append(source)
            case['sources'] = sources
        return jsonify(case)

    @app.post('/api/cases')
    def create_case():
        data, author = payload(CASE_FIELDS)
        values = case_values(data)
        timestamp = utc_now()
        with database():
            case_id = insert_record('cases', {**values, 'created_at': timestamp, 'updated_at': timestamp})
            audit(case_id, 'Caso creado', f"Se registró el caso: {values['name']}.", author, timestamp)
        return jsonify(find_record('cases', case_id)), 201

    @app.patch('/api/cases/<int:case_id>')
    def edit_case(case_id):
        data, author = payload(CASE_FIELDS, patch=True)
        values = case_values(data, patch=True)
        timestamp = utc_now()
        with database():
            database().execute('BEGIN IMMEDIATE')
            old = find_record('cases', case_id)
            description = change_description(old, values)
            update_record('cases', case_id, {**values, 'updated_at': timestamp})
            audit(case_id, 'Caso actualizado', description, author, timestamp)
        return jsonify(find_record('cases', case_id))

    @app.post('/api/cases/<int:case_id>/activities')
    def create_activity(case_id):
        data, author = payload(ACTIVITY_FIELDS)
        values = {field: _text(data, field, required=field in {'title', 'type', 'occurred_on'},
                              limit=20000 if field == 'description' else 500)
                  for field in sorted(ACTIVITY_FIELDS)}
        _enum_value(values['type'], 'type', ACTIVITY_TYPES)
        _date_value(values['occurred_on'], 'occurred_on', required=True)
        timestamp = utc_now()
        with database():
            find_record('cases', case_id)
            activity_id = insert_record('activities', {
                **values, 'case_id': case_id, 'author': author, 'created_at': timestamp,
            })
            update_record('cases', case_id, {'updated_at': timestamp})
        return jsonify(find_record('activities', activity_id)), 201

    @app.post('/api/tasks')
    def create_task():
        data, author = payload((TASK_FIELDS - {'status'}) | {'case_id'})
        values = task_values(data)
        case_id = data.get('case_id')
        if case_id is not None and (type(case_id) is not int or not 0 < case_id <= 9223372036854775807):
            raise ValidationError('case_id: usa un entero positivo o null.')
        timestamp = utc_now()
        with database():
            if case_id is not None:
                find_record('cases', case_id)
            task_id = insert_record('tasks', {**values, 'case_id': case_id, 'author': author,
                'updated_by': author, 'created_at': timestamp, 'updated_at': timestamp})
            if case_id is not None:
                audit(case_id, 'Tarea creada', f"Se registró la tarea: {values['title']}.", author, timestamp)
                update_record('cases', case_id, {'updated_at': timestamp})
        return jsonify(joined_task(task_id)), 201

    @app.patch('/api/tasks/<int:task_id>')
    def edit_task(task_id):
        data, author = payload(TASK_FIELDS, patch=True)
        values = task_values(data, patch=True)
        timestamp = utc_now()
        with database():
            database().execute('BEGIN IMMEDIATE')
            old = find_record('tasks', task_id)
            description = change_description(old, values)
            updates = {**values, 'updated_by': author, 'updated_at': timestamp}
            if 'status' in values:
                updates['completed_at'] = ((old['completed_at'] or timestamp)
                    if values['status'] == 'completada' else '')
            update_record('tasks', task_id, updates)
            if old['case_id'] is not None:
                audit(old['case_id'], 'Tarea actualizada', f"Tarea: {old['title']}\n{description}", author, timestamp)
                update_record('cases', old['case_id'], {'updated_at': timestamp})
        return jsonify(joined_task(task_id))

    def csv_export(filename, fields, rows):
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer)
        writer.writerow([label for field, label in fields])
        for row in rows:
            writer.writerow([_csv_value(row[field]) for field, label in fields])
        return Response('\ufeff' + buffer.getvalue(), mimetype='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @app.get('/api/export/cases.csv')
    def export_cases():
        fields = [(field, FIELD_LABELS.get(field, field)) for field in (
            'id', 'name', 'a_number', 'reference', 'category', 'status', 'owner',
            'country', 'court', 'hearing_date', 'notes', 'created_at', 'updated_at')]
        rows = database().execute('SELECT * FROM cases ORDER BY name COLLATE NOCASE, id').fetchall()
        return csv_export('casos.csv', fields, rows)

    @app.get('/api/export/tasks.csv')
    def export_tasks():
        fields = [(field, FIELD_LABELS.get(field, field)) for field in (
            'id', 'case_name', 'title', 'description', 'assignee', 'due_date', 'priority',
            'status', 'author', 'updated_by', 'completed_at', 'created_at', 'updated_at')]
        rows = database().execute('''SELECT t.*, c.name AS case_name FROM tasks t
            LEFT JOIN cases c ON c.id = t.case_id ''' + TASK_ORDER).fetchall()
        return csv_export('tareas.csv', fields, rows)

    @app.post('/api/import')
    def import_excel():
        if request.mimetype != 'multipart/form-data':
            return jsonify(error='Selecciona un archivo Excel para importar.'), 415
        author = _text(request.form, 'author', required=True, limit=150)
        if set(request.form) - {'author'} or set(request.files) - {'file'}:
            raise ValidationError('La importación incluye campos no permitidos.')
        uploaded = request.files.get('file')
        if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith('.xlsx'):
            raise ValidationError('Selecciona un archivo .xlsx.')
        directory = Path(app.config['DATABASE']).parent
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix='import-', suffix='.xlsx', dir=directory,
                                             delete=False) as temporary:
                temporary_path = Path(temporary.name)
                uploaded.save(temporary)
            if not zipfile.is_zipfile(temporary_path):
                raise ValidationError('El archivo no es un libro Excel válido.')
            with zipfile.ZipFile(temporary_path) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                    raise ValidationError('El libro supera los límites de tamaño de importación.')
                if any(item.flag_bits & 1 for item in entries):
                    raise ValidationError('El libro cifrado no se puede importar.')
                if not {'[Content_Types].xml', 'xl/workbook.xml'} <= set(archive.namelist()):
                    raise ValidationError('El archivo no es un libro Excel válido.')
            from importer import import_workbook
            with open(directory / '.import.lock', 'a', encoding='utf-8') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    result = import_workbook(temporary_path, app.config['DATABASE'], author=author)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return jsonify(result), 200
        except (sqlite3.Error, OSError):
            raise
        except ValidationError:
            raise
        except Exception as error:
            app.logger.warning('Importación rechazada: %s', type(error).__name__)
            raise ValidationError('No se pudo leer el libro. Verifica que sea un .xlsx válido.') from None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    return app


app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
