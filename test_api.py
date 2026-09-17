import os
import tempfile
import unittest
from unittest.mock import patch

import app


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.database = tempfile.NamedTemporaryFile(delete=False)
        self.database.close()
        app.DATABASE = self.database.name
        app.init_db()
        self.client = app.app.test_client()

    def login(self):
        response = self.client.post('/api/auth/register', json={
            'name': 'Pedro Lopez', 'email': 'pedro@example.com', 'password': 'segura123',
            'country': 'Nicaragua', 'timezone': 'America/Managua',
        })
        self.assertEqual(response.status_code, 201)

    def tearDown(self):
        os.unlink(self.database.name)

    def test_crop_crud_and_validation(self):
        self.login()
        invalid = self.client.post('/api/cultivos', json={'name': 'Maiz'})
        self.assertEqual(invalid.status_code, 400)

        response = self.client.post('/api/cultivos', json={
            'name': 'Maiz', 'type': 'Granos basicos', 'date': '2026-08-20',
            'area': 2, 'areaUnit': 'manzanas', 'seeds': 10,
        })
        self.assertEqual(response.status_code, 201)
        crop_id = response.get_json()['id']

        updated = self.client.put(f'/api/cultivos/{crop_id}', json={
            'name': 'Maiz', 'type': 'Granos basicos', 'date': '2026-08-20',
            'area': 3, 'areaUnit': 'manzanas', 'seeds': 10,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()['area'], 3)
        self.assertEqual(self.client.delete(f'/api/cultivos/{crop_id}').status_code, 204)
        self.assertEqual(self.client.delete(f'/api/cultivos/{crop_id}').status_code, 404)

    def test_profile_registration_and_validation(self):
        invalid = self.client.post('/api/auth/register', json={'name': 'P', 'email': 'correo-invalido', 'password': '123', 'country': 'Nicaragua', 'timezone': 'America/Managua'})
        self.assertEqual(invalid.status_code, 400)

        response = self.client.post('/api/auth/register', json={
            'name': 'Pedro Lopez', 'email': 'PEDRO@ejemplo.com', 'password': 'segura123', 'phone': '8888 8888', 'country': 'Nicaragua', 'timezone': 'America/Managua',
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['email'], 'pedro@ejemplo.com')

        updated = self.client.put('/api/perfil', json={
            'name': 'Pedro Lopez', 'email': 'pedro@ejemplo.com', 'phone': '', 'country': 'Honduras', 'timezone': 'America/Tegucigalpa',
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.client.get('/api/perfil').get_json()['phone'], '')

    def test_postgres_sql_placeholder_compatibility(self):
        self.assertEqual(app.sql_query('SELECT * FROM usuarios WHERE email=?'), 'SELECT * FROM usuarios WHERE email=?')
        self.assertEqual(app.sql_query('SELECT * FROM usuarios WHERE email=?', backend='postgres'), 'SELECT * FROM usuarios WHERE email=%s')
        self.assertEqual(app.sql_query('INSERT INTO usuarios (name,email) VALUES (?,?)', backend='postgres'), 'INSERT INTO usuarios (name,email) VALUES (%s,%s)')

    def test_google_oauth_entry_requires_configuration(self):
        response = self.client.get('/auth/google/login')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Google OAuth', response.get_json()['error']['message'])

    def test_google_callback_authenticates_user_and_sets_session(self):
        with patch.dict(os.environ, {'GOOGLE_CLIENT_ID': 'test-client-id', 'GOOGLE_CLIENT_SECRET': 'test-client-secret'}), patch('app.google_token_exchange', return_value={'access_token': 'token-google'}), patch('app.google_userinfo', return_value={'email': 'google.user@example.com', 'name': 'Google User', 'email_verified': True, 'sub': 'abc123'}):
            response = self.client.get('/auth/google/callback?code=abc123')
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn('user_id', session)
        with app.get_db() as connection:
            user = connection.execute('SELECT email FROM usuarios WHERE email=?', ('google.user@example.com',)).fetchone()
        self.assertIsNotNone(user)


if __name__ == '__main__':
    unittest.main()
