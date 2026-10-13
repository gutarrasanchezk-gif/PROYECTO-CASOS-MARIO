"""Private intranet access, administrator provisioning and session revocation."""

import hashlib
import re
import secrets
import sqlite3
import time

import click
from flask import g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash


PUBLIC_API_PATHS = {'/api/health', '/api/auth/session', '/api/auth/login', '/api/auth/logout'}
USER_PUBLIC_FIELDS = ('id', 'username', 'name', 'role', 'active', 'created_at', 'updated_at')
SESSION_USER_FIELDS = ('id', 'username', 'name', 'role')
# An unknown username receives the same costly password verification as a known one.
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32), method='scrypt')


def register_auth(app, database, validation_error, utc_now):
    app.config.setdefault('LOGIN_MAX_ATTEMPTS', 8)
    app.config.setdefault('LOGIN_WINDOW_SECONDS', 900)
    app.config.setdefault('LOGIN_MAX_TRACKED', 4096)
    app.config['SESSION_REFRESH_EACH_REQUEST'] = False

    def public_user(user):
        result = {field: user[field] for field in USER_PUBLIC_FIELDS}
        result['active'] = bool(result['active'])
        return result

    def csrf_token():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        return session['csrf_token']

    def session_state():
        user = g.get('user')
        has_admin = database().execute(
            "SELECT 1 FROM users WHERE active = 1 AND role = 'admin' LIMIT 1"
        ).fetchone() is not None
        return {
            'authenticated': user is not None,
            'setup_required': not has_admin,
            'csrf_token': csrf_token(),
            'user': {field: user[field] for field in SESSION_USER_FIELDS} if user else None,
        }

    def start_session(user):
        previous_token = session.get('login_session')
        if isinstance(previous_token, str):
            database().execute('DELETE FROM auth_sessions WHERE token_hash = ?',
                               (hashlib.sha256(previous_token.encode()).hexdigest(),))
        now = time.time()
        database().execute('DELETE FROM auth_sessions WHERE expires_at <= ?', (now,))
        # Keep at most 100 active sessions per user even under repeated logins.
        database().execute('''DELETE FROM auth_sessions WHERE token_hash IN (
            SELECT token_hash FROM auth_sessions WHERE user_id = ?
            ORDER BY created_at DESC LIMIT -1 OFFSET 99)''', (user['id'],))
        login_token = secrets.token_hex(32)
        database().execute('INSERT INTO auth_sessions VALUES (?, ?, ?, ?)',
            (hashlib.sha256(login_token.encode()).hexdigest(), user['id'], now,
             now + app.permanent_session_lifetime.total_seconds()))
        session.clear()
        session.permanent = True
        session['user_id'] = user['id']
        session['auth_version'] = user['auth_version']
        session['login_session'] = login_token
        session['csrf_token'] = secrets.token_hex(32)
        g.user = dict(user)

    def error(message, status):
        return jsonify(error=message), status

    @app.before_request
    def load_user_and_require_access():
        g.user = None
        user_id, auth_version = session.get('user_id'), session.get('auth_version')
        if user_id is not None:
            if type(user_id) is int and 0 < user_id <= 9223372036854775807:
                user = database().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
                login_token = session.get('login_session')
                if (user and user['active'] and type(auth_version) is int
                        and user['auth_version'] == auth_version
                        and isinstance(login_token, str) and len(login_token) == 64):
                    active_session = database().execute('''SELECT 1 FROM auth_sessions
                        WHERE token_hash = ? AND user_id = ? AND expires_at > ?''',
                        (hashlib.sha256(login_token.encode()).hexdigest(), user_id, time.time())).fetchone()
                    if active_session:
                        g.user = dict(user)
            if g.user is None:
                session.clear()
        if not request.path.startswith('/api/') or request.path in PUBLIC_API_PATHS:
            return None
        requires_auth = (app.config['AUTH_REQUIRED'] or request.path.startswith('/api/users')
                         or request.path.startswith('/api/auth/'))
        if requires_auth and g.user is None:
            return error('Inicia sesión para acceder a la información.', 401)
        if request.path.startswith('/api/users') and g.user['role'] != 'admin':
            return error('Solo un administrador puede gestionar usuarios.', 403)
        return None

    def body(allowed, required=()):
        data = request.get_json()
        if not isinstance(data, dict):
            raise validation_error('Se requiere un objeto JSON.')
        if set(data) - set(allowed):
            raise validation_error('La solicitud incluye campos no permitidos.')
        if set(required) - set(data):
            raise validation_error('Completa todos los campos obligatorios.')
        return data

    def username_value(value):
        if not isinstance(value, str):
            raise validation_error('El usuario debe ser un texto.')
        value = value.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,63}', value):
            raise validation_error('El usuario debe tener entre 3 y 64 letras sin tildes, números, puntos, guiones o guiones bajos.')
        return value

    def name_value(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 150:
            raise validation_error('Indica un nombre de hasta 150 caracteres.')
        return value.strip()

    def role_value(value):
        if not isinstance(value, str) or value not in {'admin', 'editor'}:
            raise validation_error('El rol debe ser admin o editor.')
        return value

    def password_value(value):
        if not isinstance(value, str) or not 10 <= len(value) <= 1024 or value.isspace():
            raise validation_error('La contraseña debe tener entre 10 y 1024 caracteres.')
        return value

    def get_user(user_id):
        if not 0 < user_id <= 9223372036854775807:
            raise validation_error('El identificador debe ser un entero positivo.')
        row = database().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if row is None:
            from flask import abort
            abort(404)
        return dict(row)

    def current_admin_in_transaction():
        # Permissions must still hold if another administrator changed them
        # between the initial request check and this write transaction.
        user = database().execute('SELECT * FROM users WHERE id = ?', (g.user['id'],)).fetchone()
        if not user or not user['active'] or user['role'] != 'admin' or user['auth_version'] != session.get('auth_version'):
            from flask import abort
            abort(403)

    def create_user(username, name, password, role, check_actor=False):
        username, name = username_value(username), name_value(name)
        role, password = role_value(role), password_value(password)
        password_hash = generate_password_hash(password, method='scrypt')
        timestamp = utc_now()
        connection = database()
        try:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                if check_actor:
                    current_admin_in_transaction()
                cursor = connection.execute('''INSERT INTO users
                    (username, name, password_hash, role, active, auth_version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, 1, ?, ?)''',
                    (username, name, password_hash, role, timestamp, timestamp))
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            raise validation_error('Ese nombre de usuario ya está registrado.') from None
        return get_user(user_id)

    def reserve_login_attempt():
        # One bounded bucket per IP, shared across workers, survives restarts.
        # Store only a digest; response and limiting do not disclose usernames.
        bucket = hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest()
        now = time.time()
        window = app.config['LOGIN_WINDOW_SECONDS']
        connection = database()
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('DELETE FROM login_throttle WHERE window_started <= ?', (now - window,))
            row = connection.execute('SELECT * FROM login_throttle WHERE bucket = ?', (bucket,)).fetchone()
            if row and row['attempts'] >= app.config['LOGIN_MAX_ATTEMPTS']:
                return bucket, max(1, int(row['window_started'] + window - now) + 1)
            if row is None:
                count = connection.execute('SELECT COUNT(*) FROM login_throttle').fetchone()[0]
                if count >= app.config['LOGIN_MAX_TRACKED']:
                    return bucket, max(1, int(window))
                connection.execute('INSERT INTO login_throttle VALUES (?, 1, ?)', (bucket, now))
            else:
                connection.execute('UPDATE login_throttle SET attempts = attempts + 1 WHERE bucket = ?', (bucket,))
        return bucket, None

    @app.get('/api/auth/session')
    def auth_session():
        return jsonify(session_state())

    @app.post('/api/auth/login')
    def login():
        data = body({'username', 'password'}, required={'username', 'password'})
        raw_username, password = data['username'], data['password']
        if not isinstance(raw_username, str) or not isinstance(password, str) or not password or len(password) > 1024:
            raise validation_error('Indica usuario y contraseña válidos.')
        username = raw_username.strip().lower()
        if not username or len(username) > 64:
            raise validation_error('Indica usuario y contraseña válidos.')
        bucket, retry_after = reserve_login_attempt()
        if retry_after is not None:
            response = jsonify(error='Demasiados intentos. Espera antes de volver a iniciar sesión.')
            response.status_code = 429
            response.headers['Retry-After'] = str(retry_after)
            return response
        user = database().execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        password_ok = check_password_hash(user['password_hash'] if user else _DUMMY_PASSWORD_HASH, password)
        if not user or not user['active'] or not password_ok:
            return error('Usuario o contraseña incorrectos.', 401)
        # A concurrent reset/deactivation during password verification must win.
        with database():
            database().execute('BEGIN IMMEDIATE')
            fresh = database().execute('SELECT * FROM users WHERE id = ?', (user['id'],)).fetchone()
            if not fresh['active'] or fresh['auth_version'] != user['auth_version']:
                return error('Usuario o contraseña incorrectos.', 401)
            database().execute('DELETE FROM login_throttle WHERE bucket = ?', (bucket,))
            start_session(fresh)
        return jsonify(session_state())

    @app.post('/api/auth/logout')
    def logout():
        body(set())
        login_token = session.get('login_session')
        if isinstance(login_token, str):
            with database():
                database().execute('DELETE FROM auth_sessions WHERE token_hash = ?',
                                   (hashlib.sha256(login_token.encode()).hexdigest(),))
        session.clear()
        g.user = None
        return jsonify(session_state())

    @app.post('/api/auth/change-password')
    def change_password():
        data = body({'current_password', 'new_password'}, required={'current_password', 'new_password'})
        current_password = data['current_password']
        if not isinstance(current_password, str) or not current_password or len(current_password) > 1024:
            raise validation_error('Indica la contraseña actual.')
        new_password = password_value(data['new_password'])
        if not check_password_hash(g.user['password_hash'], current_password):
            raise validation_error('La contraseña actual no es correcta.')
        new_hash = generate_password_hash(new_password, method='scrypt')
        with database():
            database().execute('BEGIN IMMEDIATE')
            user = get_user(g.user['id'])
            if not user['active'] or user['auth_version'] != session.get('auth_version'):
                session.clear()
                g.user = None
                return error('Inicia sesión nuevamente.', 401)
            database().execute('''UPDATE users SET password_hash = ?, auth_version = auth_version + 1,
                updated_at = ? WHERE id = ?''', (new_hash, utc_now(), user['id']))
            database().execute('DELETE FROM auth_sessions WHERE user_id = ?', (user['id'],))
            start_session(get_user(user['id']))
        return jsonify(session_state())

    @app.get('/api/users')
    def list_users():
        return jsonify([public_user(row) for row in database().execute('SELECT * FROM users ORDER BY username, id')])

    @app.post('/api/users')
    def add_user():
        data = body({'username', 'name', 'password', 'role'}, required={'username', 'name', 'password', 'role'})
        user = create_user(data['username'], data['name'], data['password'], data['role'], check_actor=True)
        return jsonify(public_user(user)), 201

    @app.patch('/api/users/<int:user_id>')
    def update_user(user_id):
        data = body({'name', 'role', 'active'})
        if not data:
            raise validation_error('Indica al menos un campo que deseas cambiar.')
        values = {}
        if 'name' in data:
            values['name'] = name_value(data['name'])
        if 'role' in data:
            values['role'] = role_value(data['role'])
        if 'active' in data:
            if type(data['active']) is not bool:
                raise validation_error('active debe ser true o false.')
            values['active'] = int(data['active'])
        with database():
            database().execute('BEGIN IMMEDIATE')
            current_admin_in_transaction()
            user = get_user(user_id)
            if user_id == g.user['id'] and values.get('active', user['active']) == 0:
                raise validation_error('No puedes desactivar tu propia cuenta.')
            will_be_admin = values.get('role', user['role']) == 'admin' and values.get('active', user['active']) == 1
            if user['active'] and user['role'] == 'admin' and not will_be_admin:
                admins = database().execute("SELECT COUNT(*) FROM users WHERE active = 1 AND role = 'admin'").fetchone()[0]
                if admins <= 1:
                    raise validation_error('Debe quedar al menos un administrador activo.')
            revokes_sessions = any(field in values and values[field] != user[field] for field in ('role', 'active'))
            assignments = ', '.join(f'{field} = ?' for field in values)
            if revokes_sessions:
                assignments += ', auth_version = auth_version + 1'
            database().execute(f'UPDATE users SET {assignments}, updated_at = ? WHERE id = ?',
                               [*values.values(), utc_now(), user_id])
            if revokes_sessions:
                database().execute('DELETE FROM auth_sessions WHERE user_id = ?', (user_id,))
            updated = get_user(user_id)
        if revokes_sessions and user_id == g.user['id']:
            session.clear()
            g.user = None
        return jsonify(public_user(updated))

    @app.post('/api/users/<int:user_id>/reset-password')
    def reset_password(user_id):
        data = body({'password'}, required={'password'})
        password_hash = generate_password_hash(password_value(data['password']), method='scrypt')
        with database():
            database().execute('BEGIN IMMEDIATE')
            current_admin_in_transaction()
            get_user(user_id)
            database().execute('''UPDATE users SET password_hash = ?, auth_version = auth_version + 1,
                updated_at = ? WHERE id = ?''', (password_hash, utc_now(), user_id))
            database().execute('DELETE FROM auth_sessions WHERE user_id = ?', (user_id,))
            updated = get_user(user_id)
        if user_id == g.user['id']:
            session.clear()
            g.user = None
        return jsonify(public_user(updated))

    @app.cli.command('create-admin')
    @click.option('--username', required=True, help='Usuario para iniciar sesión.')
    @click.option('--name', required=True, help='Nombre mostrado en los registros.')
    def create_admin(username, name):
        """Crear un administrador desde la terminal, sin registro público."""
        password = click.prompt('Contraseña', hide_input=True, confirmation_prompt='Repite la contraseña')
        try:
            create_user(username, name, password, 'admin')
        except validation_error as exception:
            raise click.ClickException(str(exception)) from None
        click.echo('Administrador creado. Ya puede iniciar sesión.')
