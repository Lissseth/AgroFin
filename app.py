import json
import logging
import os
import re
import smtplib
import io
from datetime import date, datetime, timedelta
from contextlib import contextmanager
from email.message import EmailMessage
from functools import wraps
from urllib import request as urllib_request
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, render_template, request, send_file, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    EXPORT_AVAILABLE = True
except ImportError:
    EXPORT_AVAILABLE = False
    logger.warning('Librerías de exportación no disponibles. Instala reportlab y openpyxl para habilitar exportación.')

import psycopg2
from psycopg2.extras import RealDictCursor

DB_INTEGRITY_ERRORS = (psycopg2.IntegrityError,)

app = Flask(__name__, static_folder='static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config['JSON_SORT_KEYS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'agrofin-local-change-this-key')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FORCE_HTTPS') == '1'
# Configuración de base de datos: Neon Postgres
DATABASE_URL = os.getenv('NEON_DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('NEON_DATABASE_URL environment variable is required')
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


def sql_query(query):
    return re.sub(r'(?<!:)\?', '%s', query)


class DatabaseConnection:
    def __init__(self, raw_connection):
        self._connection = raw_connection

    def execute(self, query, params=()):
        cursor = self._connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(sql_query(query), params)
        self._last_cursor = cursor
        return cursor

    def executescript(self, script):
        for statement in [part.strip() for part in script.split(';') if part.strip()]:
            self.execute(statement)
        return None

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
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "img-src 'self' data: https://images.pexels.com https://images.unsplash.com; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "frame-ancestors 'none'"
    )
    return response


@contextmanager
def get_db():
    connection = psycopg2.connect(DATABASE)
    wrapped = DatabaseConnection(connection)
    try:
        yield wrapped
        wrapped.commit()
    except Exception:
        wrapped.rollback()
        raise
    finally:
        wrapped.close()


def init_db():
    with get_db() as connection:
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS cultivos (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL, type TEXT NOT NULL, date TEXT NOT NULL,
                area REAL NOT NULL CHECK(area >= 0), areaUnit TEXT NOT NULL,
                seeds REAL DEFAULT 0 CHECK(seeds >= 0),
                estimated_yield REAL DEFAULT 0 CHECK(estimated_yield >= 0),
                yield_unit TEXT DEFAULT 'quintales'
            );
            CREATE TABLE IF NOT EXISTS movimientos (
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT DEFAULT '',
                country TEXT NOT NULL DEFAULT 'Nicaragua',
                timezone TEXT NOT NULL DEFAULT 'America/Managua',
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                es_premium INTEGER NOT NULL DEFAULT 0,
                paypal_subscriber_id TEXT DEFAULT '',
                premium_hasta TEXT DEFAULT NULL,
                max_crops INTEGER DEFAULT 5
            );
            CREATE TABLE IF NOT EXISTS alertas_costos (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                crop_id INTEGER,
                category TEXT,
                threshold_amount REAL NOT NULL DEFAULT 0,
                threshold_type TEXT DEFAULT 'expense',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        
        for table in ('cultivos', 'movimientos'):
            connection.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id INTEGER')
        
        connection.execute('ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS es_premium INTEGER NOT NULL DEFAULT 0')
        connection.execute('ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS paypal_subscriber_id TEXT DEFAULT \'\'')
        connection.execute('ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS premium_hasta TEXT DEFAULT NULL')
        connection.execute('ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS max_crops INTEGER DEFAULT 5')
        connection.execute('ALTER TABLE cultivos ADD COLUMN IF NOT EXISTS estimated_yield REAL DEFAULT 0')
        connection.execute('ALTER TABLE cultivos ADD COLUMN IF NOT EXISTS yield_unit TEXT DEFAULT \'quintales\'')
        
        try:
            connection.execute('ALTER TABLE alertas_costos ADD CONSTRAINT fk_alertas_user FOREIGN KEY (user_id) REFERENCES usuarios(id) ON DELETE CASCADE')
        except Exception:
            pass
        
        try:
            connection.execute('ALTER TABLE alertas_costos ADD CONSTRAINT fk_alertas_crop FOREIGN KEY (crop_id) REFERENCES cultivos(id) ON DELETE CASCADE')
        except Exception:
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


def email_configuration():
    sender = os.getenv('EMAIL_USERNAME') or os.getenv('GMAIL_USERNAME') or os.getenv('MAIL_USERNAME') or 'ruizfatima1p@gmail.com'
    password = os.getenv('EMAIL_PASSWORD') or os.getenv('GMAIL_APP_PASSWORD') or os.getenv('MAIL_PASSWORD')
    recipient = os.getenv('CONTACT_EMAIL') or os.getenv('ADMIN_EMAIL') or os.getenv('EMAIL_TO') or 'ruizfatima1p@gmail.com'
    return {
        'host': os.getenv('EMAIL_HOST', 'smtp.gmail.com'),
        'port': int(os.getenv('EMAIL_PORT', '465')),
        'username': sender,
        'password': password,
        'recipient': recipient,
    }


def send_email(to_email, subject, body, reply_to=None):
    config = email_configuration()
    if not config['username'] or not config['password']:
        logger.warning('SMTP no configurado. Se omitió el envío automático de correo.')
        return False
    try:
        message = EmailMessage()
        message['From'] = config['username']
        message['To'] = to_email
        message['Subject'] = subject
        if reply_to:
            message['Reply-To'] = reply_to
        message.set_content(body)
        with smtplib.SMTP_SSL(config['host'], config['port']) as server:
            server.login(config['username'], config['password'])
            server.send_message(message)
        return True
    except Exception:
        logger.exception('No se pudo enviar el correo electrónico.')
        return False


def send_registration_email(name, email):
    config = email_configuration()
    if not config['username'] or not config['password']:
        return False
    name = clean_text(name, 80)
    subject = 'Bienvenido a AgroFin'
    body = (
        f'Hola {name},\n\n'
        'Gracias por registrarte en AgroFin. Tu cuenta ya quedó creada y puedes comenzar a registrar cultivos, movimientos y finanzas del campo.\n\n'
        'Si necesitas ayuda, puedes escribirnos desde la sección de Ayuda de la aplicación.\n\n'
        'Saludos,\n'
        'Equipo AgroFin\n'
    )
    return send_email(email, subject, body, reply_to=config['recipient'])


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    with get_db() as connection:
        return connection.execute('SELECT id, name, email, phone, country, timezone, es_premium, paypal_subscriber_id, premium_hasta FROM usuarios WHERE id=?', (user_id,)).fetchone()


def current_user_data():
    user = current_user()
    return dict(user) if user else {}


def is_user_premium(user):
    """Verifica si un usuario es premium de forma consistente."""
    if not user:
        logger.warning('is_user_premium: user is None')
        return False
    premium_value = user.get('es_premium')
    logger.info(f'is_user_premium: user_id={user.get("id")}, es_premium={premium_value}, type={type(premium_value)}')
    # Acepta 1, True, '1', 'true' como premium
    result = bool(premium_value) and premium_value not in (0, '0', False, 'false')
    logger.info(f'is_user_premium: result={result}')
    return result


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
    except DB_INTEGRITY_ERRORS:
        return error('Ya existe una cuenta con ese correo.', 409)
    send_registration_email(data['name'], data['email'])
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


@app.post('/api/contact')
def contact_message():
    logger.info('=== Iniciando procesamiento de formulario de contacto ===')
    data, problem = payload(['name', 'email', 'reason', 'message'])
    if problem or data is None:
        logger.error(f'Error en payload: {problem}')
        return error(problem, 400)
    name = clean_text(data['name'], 80)
    email = clean_text(data['email'], 160).lower()
    reason = clean_text(data['reason'], 80)
    message = clean_text(data['message'], 1000)
    logger.info(f'Datos recibidos - Nombre: {name}, Email: {email}, Razón: {reason}')
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return error('Escribe un correo electrónico válido.', 400)
    if len(name) < 2 or len(message) < 10:
        return error('Completa nombre y mensaje con información válida.', 400)
    config = email_configuration()
    logger.info(f'Configuración SMTP - Host: {config["host"]}, Username: {config["username"]}, Recipient: {config["recipient"]}')
    recipient = config['recipient']
    if not recipient:
        logger.error('No hay destinatario configurado')
        return error('No hay un destinatario configurado para los mensajes de ayuda.', 500)
    subject = f'Contacto AgroFin · {reason} · {name}'
    body = (
        f'Nombre: {name}\n'
        f'Correo: {email}\n'
        f'Motivo: {reason}\n\n'
        'Mensaje:\n'
        f'{message}\n'
    )
    logger.info('Intentando enviar correo...')
    sent = send_email(recipient, subject, body, reply_to=email)
    if not sent:
        logger.error('No se pudo enviar el correo - verificar configuración SMTP')
        return error('No se pudo enviar tu mensaje en este momento. Inténtalo nuevamente más tarde.', 500)
    logger.info('Correo enviado exitosamente')
    return jsonify({'message': 'Tu mensaje fue enviado correctamente.'})

@app.post('/api/contacto')
def contacto():
    data, problem = payload(['subject', 'message'])
    if problem or data is None:
        return error(problem, 400)
    name = clean_text(data.get('name', 'Usuario'), 80)
    email = clean_text(data.get('email', ''), 160).lower()
    subject = clean_text(data['subject'], 120)
    message = clean_text(data['message'], 2000)
    if not email or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        return error('Escribe un correo válido.', 400)
    if len(subject) < 3 or len(message) < 5:
        return error('Asunto y mensaje deben ser más largos.', 400)
    config = email_configuration()
    recipient = config['recipient']
    if not recipient:
        return error('No hay destinatario configurado.', 500)
    sent = send_email(recipient, f'Contacto AgroFin: {subject}', f'Nombre: {name}\nCorreo: {email}\n\nMensaje:\n{message}', reply_to=email)
    if not sent:
        return error('No se pudo enviar tu mensaje.', 500)
    return jsonify({'message': 'Tu mensaje fue enviado correctamente'})

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
            user = connection.execute('SELECT id, name, email, phone, country, timezone, es_premium, paypal_subscriber_id, premium_hasta FROM usuarios WHERE email=?', (email,)).fetchone()
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
    verification_file = os.path.join(app.root_path, 'googleca945794cfdc0b13.html')
    if not os.path.exists(verification_file):
        return error('Archivo de verificación no disponible.', 404)
    return send_file(verification_file, mimetype='text/html')


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
    try:
        with get_db() as connection:
            connection.execute('UPDATE usuarios SET name=?, email=?, phone=?, country=?, timezone=? WHERE id=?', (data['name'], data['email'], data['phone'], data['country'], data['timezone'], session['user_id']))
    except DB_INTEGRITY_ERRORS:
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

    user = current_user()
    premium = is_user_premium(user)
    if not premium:
        with get_db() as connection:
            crop_count = connection.execute('SELECT COUNT(*) as count FROM cultivos WHERE user_id=?', (session['user_id'],)).fetchone()['count']
            max_crops = user.get('max_crops', 5) if user else 5
            if crop_count >= max_crops:
                return error(f'Has alcanzado el límite de {max_crops} lotes. Actualiza a Premium para tener lotes ilimitados.', 403)
    
    try:
        crop = (clean_text(data['name']), clean_text(data['type']), date.fromisoformat(data['date']).isoformat(), clean_number(data['area'], 'area'), clean_text(data['areaUnit'], 30), clean_number(data.get('seeds', 0), 'seeds'), clean_number(data.get('estimated_yield', 0), 'estimated_yield'), clean_text(data.get('yield_unit', 'quintales'), 20))
    except (ValueError, TypeError) as exc:
        return error(str(exc), 400)
    with get_db() as connection:
        cursor = connection.execute('INSERT INTO cultivos (name,type,date,area,areaUnit,seeds,estimated_yield,yield_unit,user_id) VALUES (?,?,?,?,?,?,?,?,?) RETURNING id', (*crop, session['user_id']))
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
        values = (clean_text(data['name']), clean_text(data['type']), date.fromisoformat(data['date']).isoformat(), clean_number(data['area'], 'area'), clean_text(data['areaUnit'], 30), clean_number(data.get('seeds', 0), 'seeds'), clean_number(data.get('estimated_yield', 0), 'estimated_yield'), clean_text(data.get('yield_unit', 'quintales'), 20), crop_id)
    except (ValueError, TypeError) as exc:
        return error(str(exc), 400)
    with get_db() as connection:
        cursor = connection.execute('UPDATE cultivos SET name=?,type=?,date=?,area=?,areaUnit=?,seeds=?,estimated_yield=?,yield_unit=? WHERE id=? AND user_id=?', (*values, session['user_id']))
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


@app.get('/api/premium/status')
@login_required
def get_premium_status():
    user = current_user()
    if not user:
        return error('Usuario no autenticado', 401)

    premium = is_user_premium(user)

    with get_db() as connection:
        crop_count = connection.execute('SELECT COUNT(*) as count FROM cultivos WHERE user_id=?', (session['user_id'],)).fetchone()['count']
        max_crops = user.get('max_crops', 5) if not premium else float('inf')
        can_add_more = premium or crop_count < max_crops

    # Log para depuración
    logger.info(f'Premium status for user {session["user_id"]}: es_premium={user.get("es_premium")}, premium={premium}, type={type(user.get("es_premium"))}')

    return jsonify({
        'is_premium': premium,
        'premium_until': user.get('premium_hasta'),
        'crop_count': crop_count,
        'max_crops': max_crops if not premium else 'ilimitado',
        'can_add_more_crops': can_add_more,
        'debug': {
            'es_premium_value': user.get('es_premium'),
            'es_premium_type': str(type(user.get('es_premium')))
        },
        'features': {
            'unlimited_crops': premium,
            'margin_calculation': premium,
            'export_reports': premium,
            'whatsapp_support': premium,
            'detailed_analysis': premium,
            'cost_alerts': premium,
            'new_features': premium
        }
    })


@app.post('/api/premium/activate')
@login_required
def activate_premium():
    data, problem = payload(['subscription_id', 'payment_method'])
    if problem or data is None:
        return error(problem, 400)
    
    user = current_user()
    if not user:
        return error('Usuario no autenticado', 401)
    
    payment_method = clean_text(data['payment_method'], 20)
    subscription_id = clean_text(data['subscription_id'], 100)
    
    if payment_method not in ['stripe', 'paypal', 'manual']:
        return error('Método de pago no válido', 400)
    
    try:
        with get_db() as connection:
            premium_until = (datetime.now() + timedelta(days=365)).isoformat()
            
            connection.execute('''
                UPDATE usuarios
                SET es_premium=1, premium_hasta=?, max_crops=NULL,
                    paypal_subscriber_id=?
                WHERE id=?
            ''', (premium_until, subscription_id if payment_method == 'paypal' else '', session['user_id']))

        return jsonify({
            'message': 'Suscripción Premium activada correctamente',
            'premium_until': premium_until
        })
    except Exception as e:
        logger.exception('Error al activar premium')
        return error(f'Error al activar suscripción: {str(e)}', 500)


@app.post('/api/premium/stripe/create-checkout')
@login_required
def create_stripe_checkout():
    user = current_user()
    if not user:
        return error('Usuario no autenticado', 401)
    
    stripe_api_key = os.getenv('STRIPE_SECRET_KEY')
    if not stripe_api_key:
        return error('Stripe no está configurado. Contacta al administrador.', 400)
    
    try:
        import stripe
        stripe.api_key = stripe_api_key
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'AgroFin Premium - Mensual',
                        'description': 'Lotes ilimitados, análisis avanzado y soporte prioritario'
                    },
                    'unit_amount': 1600,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{os.getenv('PUBLIC_URL', 'http://localhost:5000')}/premium/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{os.getenv('PUBLIC_URL', 'http://localhost:5000')}/premium/cancel",
            client_reference_id=str(user['id']),
            metadata={
                'user_id': str(user['id']),
                'user_email': user['email']
            }
        )
        
        return jsonify({'checkout_url': checkout_session.url})
    except ImportError:
        return error('Stripe no está instalado. Instala stripe-python para habilitar pagos.', 500)
    except Exception as e:
        logger.exception('Error al crear sesión de Stripe')
        return error(f'Error al crear sesión de pago: {str(e)}', 500)


@app.get('/premium/success')
def premium_success():
    session_id = request.args.get('session_id')
    if not session_id:
        return error('No se proporcionó session_id', 400)
    
    stripe_api_key = os.getenv('STRIPE_SECRET_KEY')
    if not stripe_api_key:
        return redirect('/?error=stripe_not_configured')
    
    try:
        import stripe
        stripe.api_key = stripe_api_key
        
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        user_id = checkout_session.client_reference_id
        
        if not user_id:
            return redirect('/?error=no_user_id')
        
        with get_db() as connection:
            premium_until = (datetime.now() + timedelta(days=365)).isoformat()
            
            connection.execute('''
                UPDATE usuarios
                SET es_premium=1, premium_hasta=?, max_crops=NULL
                WHERE id=?
            ''', (premium_until, int(user_id)))

        return redirect('/?premium=activated')
    except Exception as e:
        logger.exception('Error al procesar éxito de Stripe')
        return redirect(f'/?error={str(e)}')


@app.get('/premium/cancel')
def premium_cancel():
    return redirect('/?premium=cancelled')


@app.get('/api/premium/analysis')
@login_required
def get_premium_analysis():
    user = current_user()
    if not user or not is_user_premium(user):
        return error('Esta función es exclusiva para usuarios Premium', 403)
    
    with get_db() as connection:
        crops = connection.execute('SELECT * FROM cultivos WHERE user_id=?', (session['user_id'],)).fetchall()
        movements = connection.execute('SELECT * FROM movimientos WHERE user_id=?', (session['user_id'],)).fetchall()
    
    analysis = []
    for crop in crops:
        crop_movements = [m for m in movements if m['crop'] == crop['name']]
        total_income = sum(float(m['amount']) * float(m.get('price', 1)) for m in crop_movements if m['kind'] == 'income')
        total_expense = sum(float(m['amount']) * float(m.get('price', 1)) for m in crop_movements if m['kind'] != 'income')
        net_profit = total_income - total_expense
        
        estimated_yield = float(crop.get('estimated_yield', 0))
        yield_unit = crop.get('yield_unit', 'quintales')
        
        margin_per_unit = 0
        if estimated_yield > 0:
            margin_per_unit = net_profit / estimated_yield
        
        roi = 0
        if total_expense > 0:
            roi = ((net_profit / total_expense) * 100)
        
        analysis.append({
            'crop_id': crop['id'],
            'crop_name': crop['name'],
            'crop_type': crop['type'],
            'area': crop['area'],
            'area_unit': crop['areaUnit'],
            'total_income': total_income,
            'total_expense': total_expense,
            'net_profit': net_profit,
            'estimated_yield': estimated_yield,
            'yield_unit': yield_unit,
            'margin_per_unit': margin_per_unit,
            'margin_per_unit_formatted': f'C$ {margin_per_unit:.2f} por {yield_unit}',
            'roi_percentage': roi,
            'roi_formatted': f'{roi:.2f}%',
            'movement_count': len(crop_movements)
        })
    
    return jsonify({
        'analysis': analysis,
        'total_crops': len(crops),
        'total_movements': len(movements),
        'overall_income': sum(a['total_income'] for a in analysis),
        'overall_expense': sum(a['total_expense'] for a in analysis),
        'overall_profit': sum(a['net_profit'] for a in analysis)
    })


@app.get('/api/premium/export/pdf')
@login_required
def export_pdf():
    user = current_user()
    if not user or not is_user_premium(user):
        return error('Esta función es exclusiva para usuarios Premium', 403)

    if not EXPORT_AVAILABLE:
        return error('Librerías de exportación no disponibles', 500)

    analysis_response = get_premium_analysis()
    if isinstance(analysis_response, tuple):
        return analysis_response

    analysis_data = analysis_response.get_json()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=72, bottomMargin=72)
    elements = []
    styles = getSampleStyleSheet()

    # Header con branding
    title_style = styles['Heading1']
    title_style.alignment = TA_CENTER
    title_style.textColor = colors.HexColor('#287a50')
    elements.append(Paragraph("AgroFin - Reporte de Rentabilidad", title_style))
    elements.append(Spacer(1, 8))

    # Información del usuario
    user_info_style = styles['Normal']
    user_info_style.alignment = TA_CENTER
    elements.append(Paragraph(f"<b>Usuario:</b> {user['name']}", user_info_style))
    elements.append(Paragraph(f"<b>Email:</b> {user['email']}", user_info_style))
    elements.append(Paragraph(f"<b>País:</b> {user.get('country', 'N/A')}", user_info_style))
    elements.append(Paragraph(f"<b>Fecha:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", user_info_style))
    elements.append(Spacer(1, 16))

    # Resumen general
    elements.append(Paragraph("RESUMEN GENERAL", styles['Heading2']))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"<b>Total de Cultivos:</b> {analysis_data['total_crops']}", styles['Normal']))
    elements.append(Paragraph(f"<b>Total de Movimientos:</b> {analysis_data['total_movements']}", styles['Normal']))
    elements.append(Paragraph(f"<b>Ingresos Totales:</b> C$ {analysis_data['overall_income']:.2f}", styles['Normal']))
    elements.append(Paragraph(f"<b>Egresos Totales:</b> C$ {analysis_data['overall_expense']:.2f}", styles['Normal']))
    elements.append(Paragraph(f"<b>Utilidad Neta:</b> C$ {analysis_data['overall_profit']:.2f}", styles['Normal']))
    elements.append(Spacer(1, 16))

    # Tabla de análisis por cultivo
    elements.append(Paragraph("ANÁLISIS POR CULTIVO", styles['Heading2']))
    elements.append(Spacer(1, 6))

    data = [['Cultivo', 'Tipo', 'Área', 'Ingresos', 'Egresos', 'Utilidad', 'Margen/Unidad', 'ROI']]
    for item in analysis_data['analysis']:
        data.append([
            item['crop_name'],
            item['crop_type'],
            f"{item['area']} {item['area_unit']}",
            f"C$ {item['total_income']:.2f}",
            f"C$ {item['total_expense']:.2f}",
            f"C$ {item['net_profit']:.2f}",
            item['margin_per_unit_formatted'],
            item['roi_formatted']
        ])

    table = Table(data, colWidths=[80, 60, 50, 60, 60, 60, 80, 50])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#287a50')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
    ]))
    elements.append(table)

    # Footer
    elements.append(Spacer(1, 24))
    footer_style = styles['Normal']
    footer_style.alignment = TA_CENTER
    footer_style.fontSize = 8
    footer_style.textColor = colors.grey
    elements.append(Paragraph("Reporte generado por AgroFin - Control Agrícola", footer_style))
    elements.append(Paragraph("agrofin.onrender.com", footer_style))

    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'AgroFin_Reporte_{user["name"].replace(" ", "_")}_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )


@app.get('/api/premium/export/excel')
@login_required
def export_excel():
    user = current_user()
    if not user or not is_user_premium(user):
        return error('Esta función es exclusiva para usuarios Premium', 403)

    if not EXPORT_AVAILABLE:
        return error('Librerías de exportación no disponibles', 500)

    analysis_response = get_premium_analysis()
    if isinstance(analysis_response, tuple):
        return analysis_response

    analysis_data = analysis_response.get_json()

    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte AgroFin"

    # Header branding
    ws['A1'] = 'AgroFin - Reporte de Rentabilidad'
    ws['A1'].font = Font(bold=True, size=16, color="287a50")
    ws.merge_cells('A1:L1')

    # Información del usuario
    ws['A2'] = f'Usuario: {user["name"]}'
    ws['A2'].font = Font(bold=True)
    ws['A3'] = f'Email: {user["email"]}'
    ws['A4'] = f'País: {user.get("country", "N/A")}'
    ws['A5'] = f'Fecha: {datetime.now().strftime("%d/%m/%Y %H:%M")}'

    # Resumen general
    ws['A7'] = 'RESUMEN GENERAL'
    ws['A7'].font = Font(bold=True, size=12, color="287a50")
    ws.merge_cells('A7:L7')

    ws['A8'] = 'Total de Cultivos:'
    ws['B8'] = analysis_data['total_crops']
    ws['A9'] = 'Total de Movimientos:'
    ws['B9'] = analysis_data['total_movements']
    ws['A10'] = 'Ingresos Totales:'
    ws['B10'] = analysis_data['overall_income']
    ws['B10'].number_format = '#,##0.00'
    ws['A11'] = 'Egresos Totales:'
    ws['B11'] = analysis_data['overall_expense']
    ws['B11'].number_format = '#,##0.00'
    ws['A12'] = 'Utilidad Neta:'
    ws['B12'] = analysis_data['overall_profit']
    ws['B12'].number_format = '#,##0.00'
    ws['B12'].font = Font(bold=True, color="287a50" if analysis_data['overall_profit'] >= 0 else "FF0000")

    # Análisis por cultivo
    ws['A14'] = 'ANÁLISIS POR CULTIVO'
    ws['A14'].font = Font(bold=True, size=12, color="287a50")
    ws.merge_cells('A14:L14')

    headers = ['Cultivo', 'Tipo', 'Área', 'Unidad Área', 'Ingresos', 'Egresos', 'Utilidad', 'Rendimiento Est.', 'Unidad Rend.', 'Margen/Unidad', 'ROI %', 'Movimientos']
    ws.append(headers)

    header_fill = PatternFill(start_color="287a50", end_color="287a50", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=10)

    for cell in ws[15]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for item in analysis_data['analysis']:
        row_num = ws.max_row + 1
        ws.append([
            item['crop_name'],
            item['crop_type'],
            item['area'],
            item['area_unit'],
            item['total_income'],
            item['total_expense'],
            item['net_profit'],
            item['estimated_yield'],
            item['yield_unit'],
            item['margin_per_unit'],
            item['roi_percentage'],
            item['movement_count']
        ])

        # Formato numérico
        ws[f'E{row_num}'].number_format = '#,##0.00'
        ws[f'F{row_num}'].number_format = '#,##0.00'
        ws[f'G{row_num}'].number_format = '#,##0.00'
        ws[f'J{row_num}'].number_format = '#,##0.00'
        ws[f'K{row_num}'].number_format = '0.00'

        # Colorear utilidad
        if item['net_profit'] >= 0:
            ws[f'G{row_num}'].font = Font(color="287a50")
        else:
            ws[f'G{row_num}'].font = Font(color="FF0000")

    # Footer
    row_num = ws.max_row + 2
    ws[f'A{row_num}'] = 'Reporte generado por AgroFin - Control Agrícola'
    ws[f'A{row_num}'].font = Font(italic=True, size=8, color="808080")
    ws[f'A{row_num+1}'] = 'agrofin.onrender.com'
    ws[f'A{row_num+1}'].font = Font(italic=True, size=8, color="808080")

    # Ajustar ancho de columnas
    column_widths = [25, 15, 10, 12, 12, 12, 12, 12, 12, 15, 10, 10]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'AgroFin_Reporte_{user["name"].replace(" ", "_")}_{datetime.now().strftime("%Y%m%d")}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.get('/api/premium/alerts')
@login_required
def get_alerts():
    user = current_user()
    if not user or not is_user_premium(user):
        return error('Esta función es exclusiva para usuarios Premium', 403)
    
    with get_db() as connection:
        alerts = connection.execute('''
            SELECT a.*, c.name as crop_name 
            FROM alertas_costos a
            LEFT JOIN cultivos c ON a.crop_id = c.id
            WHERE a.user_id=? AND a.is_active=1
            ORDER BY a.created_at DESC
        ''', (session['user_id'],)).fetchall()
    
    return jsonify([dict(alert) for alert in alerts])


@app.post('/api/premium/alerts')
@login_required
def create_alert():
    user = current_user()
    if not user or not is_user_premium(user):
        return error('Esta función es exclusiva para usuarios Premium', 403)
    
    data, problem = payload(['threshold_amount', 'threshold_type'])
    if problem or data is None:
        return error(problem, 400)
    
    try:
        threshold_amount = clean_number(data['threshold_amount'], 'threshold_amount')
        threshold_type = clean_text(data.get('threshold_type', 'expense'), 20)
        crop_id = data.get('crop_id')
        category = clean_text(data.get('category', ''), 50)
        
        if threshold_type not in ['expense', 'income', 'balance']:
            return error('Tipo de umbral no válido. Debe ser: expense, income o balance', 400)
        
        with get_db() as connection:
            cursor = connection.execute('''
                INSERT INTO alertas_costos (user_id, crop_id, category, threshold_amount, threshold_type, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
                RETURNING id
            ''', (session['user_id'], crop_id, category, threshold_amount, threshold_type))
            
            created = connection.execute('SELECT * FROM alertas_costos WHERE id=?', (cursor.fetchone()['id'],)).fetchone()
        
        return jsonify(dict(created)), 201
    except (ValueError, TypeError) as exc:
        return error(str(exc), 400)


@app.delete('/api/premium/alerts/<int:alert_id>')
@login_required
def delete_alert(alert_id):
    user = current_user()
    if not user or not is_user_premium(user):
        return error('Esta función es exclusiva para usuarios Premium', 403)
    
    with get_db() as connection:
        cursor = connection.execute('UPDATE alertas_costos SET is_active=0 WHERE id=? AND user_id=?', (alert_id, session['user_id']))
    
    if cursor.rowcount == 0:
        return error('Alerta no encontrada.', 404)
    
    return '', 204


@app.get('/api/premium/alerts/check')
@login_required
def check_alerts():
    user = current_user()
    if not user or not is_user_premium(user):
        return error('Esta función es exclusiva para usuarios Premium', 403)
    
    with get_db() as connection:
        alerts = connection.execute('''
            SELECT * FROM alertas_costos 
            WHERE user_id=? AND is_active=1
        ''', (session['user_id'],)).fetchall()
        
        movements = connection.execute('SELECT * FROM movimientos WHERE user_id=?', (session['user_id'],)).fetchall()
    
    triggered_alerts = []
    
    for alert in alerts:
        relevant_movements = movements
        
        if alert['crop_id']:
            crop = connection.execute('SELECT name FROM cultivos WHERE id=?', (alert['crop_id'],)).fetchone()
            if crop:
                relevant_movements = [m for m in movements if m['crop'] == crop['name']]
        
        if alert['category']:
            relevant_movements = [m for m in relevant_movements if m['category'] == alert['category']]
        
        if alert['threshold_type'] == 'expense':
            total = sum(float(m['amount']) * float(m.get('price', 1)) for m in relevant_movements if m['kind'] != 'income')
        elif alert['threshold_type'] == 'income':
            total = sum(float(m['amount']) * float(m.get('price', 1)) for m in relevant_movements if m['kind'] == 'income')
        else:
            income = sum(float(m['amount']) * float(m.get('price', 1)) for m in relevant_movements if m['kind'] == 'income')
            expense = sum(float(m['amount']) * float(m.get('price', 1)) for m in relevant_movements if m['kind'] != 'income')
            total = income - expense
        
        if total >= float(alert['threshold_amount']):
            triggered_alerts.append({
                'alert_id': alert['id'],
                'crop_id': alert['crop_id'],
                'category': alert['category'],
                'threshold_type': alert['threshold_type'],
                'threshold_amount': alert['threshold_amount'],
                'current_amount': total,
                'message': f"¡Alerta! {alert['threshold_type']} ha alcanzado C$ {total:.2f} (umbral: C$ {alert['threshold_amount']:.2f})"
            })
    
    return jsonify({
        'has_alerts': len(triggered_alerts) > 0,
        'triggered_alerts': triggered_alerts
    })

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)


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
