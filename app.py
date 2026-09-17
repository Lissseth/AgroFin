import json
import logging
import os
import re
import sqlite3
from datetime import date
from contextlib import contextmanager
from functools import wraps
from urllib import request as urllib_request
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, render_template, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover - optional dependency for Render/PostgreSQL
    psycopg2 = None
    RealDictCursor = None

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config['JSON_SORT_KEYS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'agrofin-local-change-this-key')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FORCE_HTTPS') == '1'
DEFAULT_DATABASE_PATH = '/data/agrofin.db' if os.path.isdir('/data') else os.path.join(os.path.dirname(__file__), 'agrofin.db')
DATABASE_URL = os.getenv('DATABASE_URL') or os.getenv('DATABASE_PATH') or DEFAULT_DATABASE_PATH
DATABASE = DATABASE_URL
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def get_google_redirect_uri():
    configured = os.getenv('GOOGLE_REDIRECT_URI')
    if configured:
        return configured
    public_url = os.getenv('PUBLIC_URL') or os.getenv('RENDER_EXTERNAL_URL')
    if public_url:
        return f"{public_url.rstrip('/')}/auth/google/callback"
    return 'http://localhost:5000/auth/google/callback'


def database_backend():
    if isinstance(DATABASE, str) and re.match(r'^(postgres|postgresql)(\+.*)?://', DATABASE, re.IGNORECASE):
        return 'postgres', DATABASE
    return 'sqlite', DATABASE


def sql_query(query, backend=None):
    resolved = backend or database_backend()[0]
    if resolved == 'postgres':
        return re.sub(r'(?<!:)\?', '%s', query)
    return query


class DatabaseConnection:
    def __init__(self, raw_connection, backend):
        self._connection = raw_connection
        self.backend = backend

    def execute(self, query, params=()):
        if self.backend == 'postgres':
            cursor = self._connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute(sql_query(query, self.backend), params)
            self._last_cursor = cursor
            return cursor
        self._last_cursor = self._connection.execute(sql_query(query, self.backend), params)
        return self._last_cursor

    def executescript(self, script):
        if self.backend == 'postgres':
            for statement in [part.strip() for part in script.split(';') if part.strip()]:
                self.execute(statement)
            return None
        return self._connection.executescript(script)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


@app.before_request
def require_https_in_production():
    if os.getenv('FORCE_HTTPS') == '1' and not request.is_secure:
        return redirect(request.url.replace('http://', 'https://', 1), code=308)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Content-Security-Policy', "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com")
    return response


@contextmanager
def get_db():
    backend, database_path = database_backend()
    if backend == 'postgres':
        if psycopg2 is None:
            raise RuntimeError('psycopg2 is required when using PostgreSQL connections.')
        connection = psycopg2.connect(database_path, sslmode='require')
        wrapped = DatabaseConnection(connection, backend)
    else:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        wrapped = DatabaseConnection(connection, backend)
    try:
        yield wrapped
        wrapped.commit()
    except Exception:
        wrapped.rollback()
        raise
    finally:
        wrapped.close()


def init_db():
    backend, _ = database_backend()
    integer_primary_key = 'SERIAL PRIMARY KEY' if backend == 'postgres' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    with get_db() as connection:
        connection.executescript(f'''
            CREATE TABLE IF NOT EXISTS cultivos (
                id {integer_primary_key},
                name TEXT NOT NULL, type TEXT NOT NULL, date TEXT NOT NULL,
                area REAL NOT NULL CHECK(area >= 0), areaUnit TEXT NOT NULL,
                seeds REAL DEFAULT 0 CHECK(seeds >= 0)
            );
            CREATE TABLE IF NOT EXISTS movimientos (
                id {integer_primary_key},
                kind TEXT NOT NULL, crop TEXT NOT NULL, concept TEXT NOT NULL,
                date TEXT NOT NULL, category TEXT NOT NULL, amount REAL NOT NULL CHECK(amount >= 0),
                unit TEXT NOT NULL, price REAL DEFAULT 0 CHECK(price >= 0)
            );
            CREATE TABLE IF NOT EXISTS perfil (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                id {integer_primary_key},
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT DEFAULT '',
                country TEXT NOT NULL DEFAULT 'Nicaragua',
                timezone TEXT NOT NULL DEFAULT 'America/Managua',
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        for table in ('cultivos', 'movimientos'):
            try:
                connection.execute(f'ALTER TABLE {table} ADD COLUMN user_id INTEGER')
            except Exception as exc:
                if backend == 'sqlite' and not isinstance(exc, sqlite3.OperationalError):
                    raise
                if backend == 'postgres' and getattr(exc, 'pgcode', None) != '42701':
                    raise
                pass


def error(message, status):
    return jsonify({'error': {'message': message, 'status': status}}), status


def payload(required):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, 'El cuerpo debe ser JSON.'
    missing = [field for field in required if not str(data.get(field, '')).strip()]
    if missing:
        return None, 'Faltan campos obligatorios: ' + ', '.join(missing)
    return data, None


def clean_text(value, maximum=120):
    value = str(value).strip()
    return value[:maximum]


def clean_number(value, field):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field} debe ser numérico.')
    if number < 0:
        raise ValueError(f'{field} no puede ser negativo.')
    return number


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    with get_db() as connection:
        return connection.execute('SELECT id, name, email, phone, country, timezone FROM usuarios WHERE id=?', (user_id,)).fetchone()


def current_user_data():
    user = current_user()
    return dict(user) if user else {}


def login_required(function):
    @wraps(function)
    def secured(*args, **kwargs):
        if current_user() is None:
            return error('Debes iniciar sesión para continuar.', 401)
        return function(*args, **kwargs)
    return secured


def account_payload():
    data, problem = payload(['name', 'email', 'password', 'country', 'timezone'])
    if problem or data is None:
        return None, problem
    name = clean_text(data['name'], 80)
    email = clean_text(data['email'], 160).lower()
    password = str(data['password'])
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return None, 'Escribe un correo electrónico válido.'
    if len(name) < 2:
        return None, 'El nombre debe tener al menos 2 caracteres.'
    if len(password) < 8:
        return None, 'La contraseña debe tener al menos 8 caracteres.'
    return {'name': name, 'email': email, 'password': password,
            'phone': clean_text(data.get('phone', ''), 30),
            'country': clean_text(data['country'], 60),
            'timezone': clean_text(data['timezone'], 80)}, None


@app.post('/api/auth/register')
def register():
    data, problem = account_payload()
    if problem or data is None:
        return error(problem, 400)
    try:
        with get_db() as connection:
            cursor = connection.execute('''INSERT INTO usuarios (name,email,phone,country,timezone,password_hash)
                VALUES (?,?,?,?,?,?) RETURNING id''', (data['name'], data['email'], data['phone'], data['country'], data['timezone'], generate_password_hash(data['password'])))
            session['user_id'] = cursor.fetchone()['id']
    except sqlite3.IntegrityError:
        return error('Ya existe una cuenta con ese correo.', 409)
    return jsonify(current_user_data()), 201


@app.post('/api/auth/login')
def login():
    data, problem = payload(['email', 'password'])
    if problem or data is None:
        return error(problem, 400)
    with get_db() as connection:
        user = connection.execute('SELECT * FROM usuarios WHERE email=?', (clean_text(data['email'], 160).lower(),)).fetchone()
    if user is None or not check_password_hash(user['password_hash'], str(data['password'])):
        return error('Correo o contraseña incorrectos.', 401)
    session.clear()
    session['user_id'] = user['id']
    return jsonify(current_user_data())


@app.post('/api/auth/logout')
def logout():
    session.clear()
    return '', 204


@app.get('/auth/google/login')
def google_login():
    client_id = os.getenv('GOOGLE_CLIENT_ID')
    if not client_id:
        return error('Google OAuth no está configurado. Define GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET en el proveedor de despliegue.', 400)
    google_auth_url = 'https://accounts.google.com/o/oauth2/v2/auth'
    params = {
        'client_id': client_id,
        'redirect_uri': get_google_redirect_uri(),
        'response_type': 'code',
        'scope': 'openid email profile',
        'access_type': 'offline',
        'prompt': 'consent',
    }
    return redirect(f'{google_auth_url}?{urlencode(params)}')


def google_token_exchange(code):
    payload = urlencode({
        'code': code,
        'client_id': os.getenv('GOOGLE_CLIENT_ID'),
        'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
        'redirect_uri': get_google_redirect_uri(),
        'grant_type': 'authorization_code',
    }).encode('utf-8')
    request_obj = urllib_request.Request(
        'https://oauth2.googleapis.com/token',
        data=payload,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    with urllib_request.urlopen(request_obj, timeout=20) as response:
        return json.loads(response.read().decode('utf-8'))


def google_userinfo(access_token):
    request_obj = urllib_request.Request(
        'https://www.googleapis.com/oauth2/v3/userinfo',
        headers={'Authorization': f'Bearer {access_token}'},
    )
    with urllib_request.urlopen(request_obj, timeout=20) as response:
        return json.loads(response.read().decode('utf-8'))


@app.get('/auth/google/callback')
def google_callback():
    if not os.getenv('GOOGLE_CLIENT_ID') or not os.getenv('GOOGLE_CLIENT_SECRET'):
        return error('Google OAuth no está configurado. Añade CLIENT_ID y CLIENT_SECRET y luego vuelve a intentar.', 400)
    if request.args.get('error'):
        return error(f"Google rechazó la autorización: {request.args.get('error_description', request.args['error'])}", 400)
    code = request.args.get('code')
    if not code:
        return error('No se recibió el código de autorización de Google.', 400)
    try:
        token_data = google_token_exchange(code)
        access_token = token_data.get('access_token')
        if not access_token:
            return error('Google no devolvió un token de acceso válido.', 400)
        userinfo = google_userinfo(access_token)
        email = clean_text(userinfo.get('email', ''), 160).lower()
        if not email or userinfo.get('email_verified') is not True:
            return error('La cuenta de Google no está verificada para iniciar sesión.', 400)
        with get_db() as connection:
            user = connection.execute('SELECT id, name, email, phone, country, timezone FROM usuarios WHERE email=?', (email,)).fetchone()
            if user is None:
                name = clean_text(userinfo.get('name') or userinfo.get('given_name') or 'Usuario Google', 80)
                country = clean_text(os.getenv('DEFAULT_COUNTRY', 'Nicaragua'), 60)
                timezone_name = clean_text(os.getenv('DEFAULT_TIMEZONE', 'America/Managua'), 80)
                cursor = connection.execute(
                    'INSERT INTO usuarios (name,email,phone,country,timezone,password_hash) VALUES (?,?,?,?,?,?)',
                    (name, email, '', country, timezone_name, generate_password_hash(f"google-oauth-{userinfo.get('sub', 'user')}-{os.urandom(8).hex()}")),
                )
                user_id = cursor.lastrowid
            else:
                user_id = user['id']
        session.clear()
        session['user_id'] = user_id
        return redirect('/')
    except Exception as exc:
        logger.exception('Error en OAuth de Google')
        return error('No se pudo validar el token de Google.', 400)


@app.get('/api/auth/me')
def me():
    user = current_user()
    return jsonify(dict(user) if user else None)


@app.get('/health')
def health():
    return jsonify({'status': 'ok'})


@app.get('/googleca945794cfdc0b13.html')
def google_verification():
    return send_file('googleca945794cfdc0b13.html', mimetype='text/html')


@app.get('/robots.txt')
def robots_txt():
    return send_file('robots.txt', mimetype='text/plain')


@app.get('/sitemap.xml')
def sitemap_xml():
    return send_file('sitemap.xml', mimetype='application/xml')


def profile_payload():
    data, problem = payload(['name', 'email'])
    if problem or data is None:
        return None, problem
    name = clean_text(data['name'], 80)
    email = clean_text(data['email'], 160).lower()
    phone = clean_text(data.get('phone', ''), 30)
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return None, 'Escribe un correo electrónico válido.'
    if len(name) < 2:
        return None, 'El nombre debe tener al menos 2 caracteres.'
    return {'name': name, 'email': email, 'phone': phone}, None


def settings_payload():
    data, problem = payload(['name', 'email', 'country', 'timezone'])
    if problem or data is None:
        return None, problem
    profile, profile_problem = profile_payload_from(data)
    if profile_problem or profile is None:
        return None, profile_problem
    profile['country'] = clean_text(data['country'], 60)
    profile['timezone'] = clean_text(data['timezone'], 80)
    return profile, None


def profile_payload_from(data):
    name = clean_text(data['name'], 80)
    email = clean_text(data['email'], 160).lower()
    phone = clean_text(data.get('phone', ''), 30)
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return None, 'Escribe un correo electrónico válido.'
    if len(name) < 2:
        return None, 'El nombre debe tener al menos 2 caracteres.'
    return {'name': name, 'email': email, 'phone': phone}, None


@app.get('/api/perfil')
@login_required
def get_profile():
    return jsonify(current_user_data())


@app.put('/api/perfil')
@login_required
def save_profile():
    data, problem = settings_payload()
    if problem or data is None:
        return error(problem, 400)
    with get_db() as connection:
        try:
            connection.execute('UPDATE usuarios SET name=?, email=?, phone=?, country=?, timezone=? WHERE id=?', (data['name'], data['email'], data['phone'], data['country'], data['timezone'], session['user_id']))
        except sqlite3.IntegrityError:
            return error('Ya existe una cuenta con ese correo.', 409)
    return jsonify(current_user_data())


@app.get('/api/cultivos')
@login_required
def list_crops():
    with get_db() as connection:
        rows = connection.execute('SELECT * FROM cultivos WHERE user_id=? ORDER BY id DESC', (session['user_id'],)).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post('/api/cultivos')
@login_required
def create_crop():
    data, problem = payload(['name', 'type', 'date', 'area', 'areaUnit'])
    if problem or data is None:
        return error(problem, 400)
    try:
        crop = (clean_text(data['name']), clean_text(data['type']), date.fromisoformat(data['date']).isoformat(), clean_number(data['area'], 'area'), clean_text(data['areaUnit'], 30), clean_number(data.get('seeds', 0), 'seeds'))
    except (ValueError, TypeError) as exc:
        return error(str(exc), 400)
    with get_db() as connection:
        cursor = connection.execute('INSERT INTO cultivos (name,type,date,area,areaUnit,seeds,user_id) VALUES (?,?,?,?,?,?,?) RETURNING id', (*crop, session['user_id']))
        created = connection.execute('SELECT * FROM cultivos WHERE id = ?', (cursor.fetchone()['id'],)).fetchone()
    logger.info('Cultivo creado id=%s nombre=%s', created['id'], created['name'])
    return jsonify(dict(created)), 201


@app.put('/api/cultivos/<int:crop_id>')
@login_required
def update_crop(crop_id):
    data, problem = payload(['name', 'type', 'date', 'area', 'areaUnit'])
    if problem or data is None:
        return error(problem, 400)
    try:
        values = (clean_text(data['name']), clean_text(data['type']), date.fromisoformat(data['date']).isoformat(), clean_number(data['area'], 'area'), clean_text(data['areaUnit'], 30), clean_number(data.get('seeds', 0), 'seeds'), crop_id)
    except (ValueError, TypeError) as exc:
        return error(str(exc), 400)
    with get_db() as connection:
        cursor = connection.execute('UPDATE cultivos SET name=?,type=?,date=?,area=?,areaUnit=?,seeds=? WHERE id=? AND user_id=?', (*values, session['user_id']))
        if cursor.rowcount == 0:
            return error('Cultivo no encontrado.', 404)
        updated = connection.execute('SELECT * FROM cultivos WHERE id=?', (crop_id,)).fetchone()
    return jsonify(dict(updated))


@app.delete('/api/cultivos/<int:crop_id>')
@login_required
def delete_crop(crop_id):
    with get_db() as connection:
        cursor = connection.execute('DELETE FROM cultivos WHERE id=? AND user_id=?', (crop_id, session['user_id']))
    if cursor.rowcount == 0:
        return error('Cultivo no encontrado.', 404)
    logger.info('Cultivo eliminado id=%s', crop_id)
    return '', 204


@app.get('/api/movimientos')
@login_required
def list_movements():
    with get_db() as connection:
        rows = connection.execute('SELECT * FROM movimientos WHERE user_id=? ORDER BY id DESC', (session['user_id'],)).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post('/api/movimientos')
@login_required
def create_movement():
    data, problem = payload(['kind', 'crop', 'concept', 'date', 'category', 'amount', 'unit'])
    if problem or data is None:
        return error(problem, 400)
    try:
        movement = (clean_text(data['kind'], 20), clean_text(data['crop']), clean_text(data['concept']), date.fromisoformat(data['date']).isoformat(), clean_text(data['category'], 50), clean_number(data['amount'], 'amount'), clean_text(data['unit'], 30), clean_number(data.get('price', 0), 'price'))
    except (ValueError, TypeError) as exc:
        return error(str(exc), 400)
    with get_db() as connection:
        cursor = connection.execute('INSERT INTO movimientos (kind,crop,concept,date,category,amount,unit,price,user_id) VALUES (?,?,?,?,?,?,?,?,?) RETURNING id', (*movement, session['user_id']))
        created = connection.execute('SELECT * FROM movimientos WHERE id=?', (cursor.fetchone()['id'],)).fetchone()
    logger.info('Movimiento creado id=%s tipo=%s', created['id'], created['kind'])
    return jsonify(dict(created)), 201


@app.delete('/api/movimientos/<int:movement_id>')
@login_required
def delete_movement(movement_id):
    with get_db() as connection:
        cursor = connection.execute('DELETE FROM movimientos WHERE id=? AND user_id=?', (movement_id, session['user_id']))
    if cursor.rowcount == 0:
        return error('Movimiento no encontrado.', 404)
    return '', 204

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/terms')
def terms():
    return render_template('terms.html')


if __name__ == '__main__':
    init_db()
    app.run(
        host=os.getenv('HOST', '0.0.0.0'),
        port=int(os.getenv('PORT', '5000')),
        debug=os.getenv('FLASK_DEBUG', '0') == '1',
        threaded=True,
    )


init_db()
