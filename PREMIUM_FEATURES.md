# Características Premium - AgroFin

## Resumen de Implementación

Se ha implementado completamente el sistema de suscripción Premium con todas las características solicitadas.

## Características Implementadas

### 1. Lotes Ilimitados
- **Usuarios gratuitos**: Máximo 5 lotes (configurable en DB)
- **Usuarios Premium**: Lotes ilimitados
- **Validación**: El sistema verifica el límite antes de crear nuevos lotes

### 2. Cálculo de Margen de Utilidad por Quintal/Arroba
- Campo nuevo en cultivos: `estimated_yield` (rendimiento estimado)
- Campo nuevo en cultivos: `yield_unit` (quintales, arrobas, toneladas)
- Cálculo automático de margen por unidad de rendimiento
- ROI (Return on Investment) por cultivo

### 3. Exportación de Reportes en PDF/Excel
- **Endpoint**: `/api/premium/export/pdf` - Genera reporte PDF detallado
- **Endpoint**: `/api/premium/export/excel` - Genera reporte Excel con formato
- Incluye: ingresos, egresos, utilidad, márgenes, ROI por cultivo
- Solo disponible para usuarios Premium

### 4. Soporte Prioritario por WhatsApp
- Botón directo en el modal de planes Premium
- Enlace preconfigurado con mensaje de activación
- Indicador visual de estado Premium en el perfil

### 5. Análisis de Rentabilidad Detallado
- **Endpoint**: `/api/premium/analysis`
- Vista dedicada en el frontend: "Análisis Premium"
- Tabla con métricas por cultivo:
  - Ingresos totales
  - Egresos totales
  - Utilidad neta
  - Margen por unidad (quintal/arroba)
  - ROI porcentual
- Resumen general de la operación

### 6. Alertas de Costos y Gastos
- **Endpoints**:
  - `GET /api/premium/alerts` - Listar alertas
  - `POST /api/premium/alerts` - Crear alerta
  - `DELETE /api/premium/alerts/<id>` - Eliminar alerta
  - `GET /api/premium/alerts/check` - Verificar alertas activadas
- Tipos de alertas:
  - Gastos totales (expense)
  - Ingresos totales (income)
  - Balance (income - expense)
- Configuración por cultivo o general
- Umbral personalizable en córdobas

### 7. Acceso a Nuevas Funciones
- Badge de estado Premium en el perfil
- Vista de análisis exclusiva Premium
- Botones de exportación bloqueados para usuarios gratuitos
- Mensajes de upgrade estratégicos en la UI

## Integración con Pasarela de Pago

### Stripe (Tarjeta)
- **Endpoint**: `/api/premium/stripe/create-checkout`
- Crea sesión de checkout de Stripe
- Precio: $16 USD/mes
- Plan: Suscripción mensual recurrente
- Callbacks:
  - `/premium/success` - Activación exitosa
  - `/premium/cancel` - Cancelación por usuario

### PayPal
- Preparado para integración
- Campo `paypal_subscriber_id` en tabla usuarios
- Endpoint `/api/premium/activate` recibe ID de suscripción

### WhatsApp (Manual)
- Botón en modal de planes
- Contacto directo con desarrolladora
- Activación manual vía backend

## Configuración Requerida

### Variables de Entorno
Agregar al `.env`:
```
STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key_here
STRIPE_PUBLISHABLE_KEY=pk_test_your_stripe_publishable_key_here
```

### Dependencias Instaladas
- `reportlab>=4.0.0` - Generación de PDF
- `openpyxl>=3.1.0` - Generación de Excel
- `pandas>=2.0.0` - Análisis de datos
- `stripe>=8.0.0` - Pasarela de pago

## Esquema de Base de Datos

### Tablas Nuevas

**suscripciones**
- id, user_id, plan_type
- stripe_subscription_id, paypal_subscription_id
- amount, currency, status
- current_period_start, current_period_end
- cancel_at_period_end, created_at, updated_at

**alertas_costos**
- id, user_id, crop_id
- category, threshold_amount, threshold_type
- is_active, created_at

### Campos Nuevos en Tablas Existentes

**usuarios**
- max_crops (default: 5)
- es_premium (default: 0)
- premium_hasta (fecha)
- paypal_subscriber_id

**cultivos**
- estimated_yield (rendimiento estimado)
- yield_unit (quintales, arrobas, toneladas)

## Endpoints API Premium

### Verificación de Estado
```
GET /api/premium/status
```
Retorna: is_premium, crop_count, max_crops, can_add_more_crops, features

### Análisis
```
GET /api/premium/analysis
```
Retorna: análisis detallado por cultivo con márgenes y ROI

### Exportación
```
GET /api/premium/export/pdf
GET /api/premium/export/excel
```
Genera y descarga reportes

### Alertas
```
GET /api/premium/alerts
POST /api/premium/alerts
DELETE /api/premium/alerts/<id>
GET /api/premium/alerts/check
```
Gestión de alertas de costos

### Activación
```
POST /api/premium/activate
POST /api/premium/stripe/create-checkout
```
Activación de suscripción

## Frontend

### Nueva Vista: Análisis Premium
- Accesible desde menú lateral y móvil
- Solo visible para usuarios Premium
- Muestra:
  - Resumen financiero general
  - Tabla de análisis por cultivo
  - Botones de exportación PDF/Excel
  - Gestión de alertas

### Modal de Planes Premium
- Comparación de planes (Básico vs Premium)
- Botón de pago con Stripe
- Botón de contacto por WhatsApp
- Lista completa de características

### Formulario de Cultivos
- Campos nuevos: Rendimiento estimado y Unidad
- Visible para todos (para futura referencia)
- Utilizado en cálculos Premium

## Flujos de Usuario

### Usuario Gratuito
1. Puede crear hasta 5 lotes
2. Ve banners de upgrade en finanzas y tareas
3. No puede acceder a vista de análisis
4. Al intentar crear más de 5 lotes: mensaje de error con oferta Premium

### Usuario Premium
1. Lotes ilimitados
2. Acceso completo a vista de análisis
3. Puede exportar reportes PDF/Excel
4. Puede configurar alertas de costos
5. Badge "Premium" en perfil
6. Botones de exportación activos

### Activación de Premium
1. Usuario abre modal de planes
2. Elige método de pago:
   - Stripe: Redirigido a checkout, pago, callback activa
   - WhatsApp: Contacta desarrolladora, activación manual
3. Sistema actualiza `es_premium=1` en DB
4. UI actualiza automáticamente al recargar

## Pruebas Recomendadas

1. **Crear cuenta gratuita**: Verificar límite de 5 lotes
2. **Intentar crear 6to lote**: Debe mostrar error con oferta Premium
3. **Activar Premium manualmente** (vía DB o API):
   ```sql
   UPDATE usuarios SET es_premium=1, max_crops=NULL WHERE id=<user_id>;
   ```
4. **Verificar vista de análisis**: Debe cargar datos
5. **Configurar alerta**: Crear y verificar que aparece en lista
6. **Exportar reportes**: Probar PDF y Excel
7. **Verificar cálculos**: Margen por quintal, ROI

## Deployment

### Render.com
Variables agregadas en `render.yaml`:
- STRIPE_SECRET_KEY (sync: false)
- STRIPE_PUBLISHABLE_KEY (sync: false)

### Local
Configurar en `.env` o variables de entorno del sistema.

## Notas Importantes

1. **Stripe**: Necesita cuenta de Stripe y configuración de precios en dashboard
2. **PDF/Excel**: Requiere las librerías instaladas (ya agregadas a requirements.txt)
3. **Base de datos**: El esquema se actualiza automáticamente en `init_db()`
4. **Seguridad**: Los endpoints Premium están protegidos con `@login_required` y verificación de `es_premium`
5. **Fallback**: Si Stripe no está configurado, el botón muestra error y sugiere WhatsApp

## Soporte

Para activar suscripciones manualmente o problemas con pagos:
- WhatsApp: +505 8947 6244
- Email: ruizfatima1p@gmail.com

## Próximas Mejoras (Opcionales)

- Webhooks de Stripe para renovaciones automáticas
- Panel de administración para activar Premium manualmente
- Historial de pagos y facturas
- Planes anuales con descuento
- Integración completa con PayPal (API)
- App móvil nativa con autenticación Premium
