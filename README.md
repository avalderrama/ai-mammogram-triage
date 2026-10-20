# ai-mammogram-triage

Servidor **MCP** que hace un triaje orientativo de mamografías (vistas CC/MLO) en tres grupos BI-RADS
(1-2, 3 y 4-5) y asigna a cada mama un nivel de prioridad:

| Nivel | Significado | Criterio de calibración |
|---|---|---|
| `high_priority` | Leer primero | Como máximo un 5 % de falsas alarmas en estudios BI-RADS 1-2/3 |
| `review` | Mirar con atención | Junto con `high_priority`, detecta el 85 % de los estudios BI-RADS 4-5 |
| `no_suspicious_findings` | Sin hallazgos sospechosos para el modelo | — |

> ⚠️ **Herramienta de investigación.** No es un dispositivo médico, no está validada clínicamente y nunca
> sustituye la lectura del radiólogo.

## Qué hace cada análisis

1. **Tapa los datos personales** dibujados sobre la imagen (nombres, fechas, identificadores) con OCR.
   El texto leído nunca se devuelve, ni se guarda, ni se registra.
2. **Comprueba que sea una mamografía**: rechaza ecografías, resonancias, fotos sin relación y avisa de
   problemas de calidad de la foto (borrosa, reflejos, baja resolución, mama pequeña).
3. **Clasifica cada mama** con un conjunto de modelos globales (512 y 1024 px). Si la imagen tiene al menos
   1500 px de alto, añade un **modelo de parches a 2048 px** (mejor con calcificaciones) y devuelve un
   **mapa de calor** con las zonas sospechosas.
4. **Junta las vistas de la misma mama** (por ejemplo RCC + RMLO) y aplica los umbrales.

Las imágenes **no se guardan** en ningún momento: se procesan en memoria y se descartan.

## Herramientas MCP

| Herramienta | Uso |
|---|---|
| `mammo_analyze_mammogram` | Triaje completo. Devuelve resumen en español (o JSON), `structuredContent` y mapas de calor como imágenes. |
| `mammo_check_is_mammogram` | Solo verifica si la imagen es una mamografía utilizable y su calidad. |
| `mammo_mask_personal_data` | Devuelve la imagen con el texto tapado (PNG) y el número de zonas tapadas. |

Entrada común: `image_base64` (admite `data:image/...;base64,`) o `image_path` (solo en modo `stdio`).
Opcionales: `panel_count` (vistas lado a lado), `sides` (`R`/`L`), `views` (`CC`/`MLO`),
`include_heatmap`, `response_format` (`markdown` o `json`).

## Rendimiento (prueba oficial de VinDr-Mammo, mitad de validación no usada para calibrar)

| Modo | AUC BI-RADS 4-5 | 4-5 → alta / revisar / sin hallazgos | 1-2 → alta / revisar / sin hallazgos |
|---|---|---|---|
| Global + parches (≥ 1500 px) | 0,89 | 70 % / 17 % / 13 % | 7 % / 21 % / 72 % |
| Solo global (fotos de baja resolución) | — | 53 % / 30 % / 17 % | 3 % / 29 % / 68 % |

Las fotos de una pantalla o de una placa rinden peor que el archivo original: úsese siempre la mejor
imagen disponible.

## Instalación

Requiere Python 3.11 y, opcionalmente, una GPU NVIDIA.

```bash
pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu126
pip install -e ".[dev]"
```

### Pesos del modelo

Los pesos **no se publican** en este repositorio: se entrenaron con VinDr-Mammo, cuyo acuerdo de uso de
PhysioNet no permite redistribuirlos. Quien tenga acceso al paquete exportado por el repositorio privado de
entrenamiento lo indica en `MAMMO_MODELS_DIR` (carpeta con `manifest.json`, cuatro `.pt` y
`fusion_calibration.json`). Al arrancar se comprueba el SHA-256 de cada archivo.

## Uso

### Local con Claude Desktop (stdio)

```json
{
  "mcpServers": {
    "mammo-triage": {
      "command": "C:\\ruta\\al\\venv\\Scripts\\mammo-triage.exe",
      "args": ["stdio"],
      "env": { "MAMMO_MODELS_DIR": "C:\\ruta\\al\\bundle\\v1" }
    }
  }
}
```

### Por HTTP (para la app PWA o un túnel HTTPS)

```bash
mammo-triage new-key
```

Copie `.env.example` a `.env`, ponga la clave generada en `MAMMO_API_KEY` y arranque:

```bash
mammo-triage http
```

El endpoint MCP queda en `http://127.0.0.1:8765/mcp` (streamable HTTP, sin estado, respuestas JSON) y
`/health` responde `{"status": "ok"}`.

Protecciones del modo HTTP:

- Clave obligatoria (`Authorization: Bearer <clave>` o `X-API-Key`), de al menos 24 caracteres,
  comparada en tiempo constante.
- Límite de tamaño de la petición (413) y de peticiones por minuto y cliente en `/mcp` (429 con `Retry-After`;
  cada análisis consume unas 3 peticiones MCP, por eso el valor por defecto es 60).
- Como mucho 4 análisis en curso o en espera; por encima responde `server_busy` en lugar de acumular imágenes.
- Imágenes: solo PNG, JPEG, WebP, TIFF y BMP reconocidos por su contenido (no por lo que declare el cliente) y
  como máximo 60 megapíxeles, comprobado antes de decodificar.
- Los pesos se cargan con `torch.load(weights_only=True)`: un archivo de pesos manipulado no puede ejecutar código.
- CORS solo para los orígenes de `MAMMO_ALLOWED_ORIGINS` y protección contra *DNS rebinding*
  (`MAMMO_ALLOWED_HOSTS`).
- Escucha solo en `127.0.0.1`. Para exponerlo, use un túnel HTTPS (por ejemplo Cloudflare Tunnel),
  añada su dominio a `MAMMO_ALLOWED_HOSTS` y active `MAMMO_TRUST_PROXY_HEADERS=true` para limitar por la IP
  real del cliente (el servidor se niega a arrancar si falta). La cabecera `CF-Connecting-IP` solo se acepta
  cuando la conexión llega desde la propia máquina (el demonio del túnel).

### Arrancar el piloto con túneles HTTPS

Requiere `cloudflared` (ejecutable oficial de Cloudflare; el script asume
`%USERPROFILE%	ools\cloudflared6.10.0\cloudflared.exe`) y la app en la carpeta hermana
`ai-mammogram-triage-app`. En PowerShell:

```powershell
.\scripts\start_pilot.ps1
```

El script compila y sirve la PWA y abre dos túneles rápidos (uno para la app y otro para el servidor). Luego
arranca el servidor ya configurado y muestra las dos direcciones que hay que pasar a los médicos, junto con la
clave (por un canal privado). Las direcciones cambian en cada arranque. `Ctrl+C` lo detiene todo. No deje
imágenes de prueba en `dist/` de la app mientras el túnel esté abierto: todo lo que hay ahí es público.

### Recomendaciones para el piloto

- La clave es compartida: si se filtra, cualquiera puede usar el servicio. Para algo más que una prueba corta,
  ponga **Cloudflare Access** (código por correo o *service tokens*) delante del túnel y rote la clave
  (`mammo-triage new-key`) al terminar cada ronda de pruebas.
- El borrado de datos personales por OCR puede no detectar texto muy tenue o muy grande: revise las imágenes
  enmascaradas antes de compartirlas.
- Las fotos llegan con sus metadatos (EXIF); el servidor no las guarda ni las registra.
- Las dependencias de Python se declaran con rangos; para un despliegue reproducible genere un archivo de
  bloqueo con *hashes* (por ejemplo `uv pip compile --generate-hashes`).

## Arquitectura

Arquitectura de cebolla, un paquete por anillo en `src/`:

| Paquete | Anillo |
|---|---|
| `mammo_domain` | Dominio: probabilidades, puntuaciones, política de triaje, evaluación por mama y estudio |
| `mammo_application` | Casos de uso, puertos y DTOs |
| `mammo_infrastructure` | PyTorch, EasyOCR, decodificación de imágenes, paquete de modelos |
| `mammo_mcp`, `mammo_api` | Presentación: servidor MCP y exposición HTTP segura |
| `mammo_host` | Composition root, configuración y CLI |

Las reglas de dependencia se comprueban con `lint-imports` y `tests/architecture`.

## Pruebas

```bash
pytest
```

Pruebas de integración con los modelos reales (necesitan el paquete de pesos y una biblioteca local de
imágenes de prueba, que no se publica):

```bash
pytest -m integration
```

## Limitaciones conocidas

- Entrenado sobre todo con mamografías vietnamitas (VinDr-Mammo); el rendimiento en otras poblaciones y
  equipos no está validado.
- El filtro acepta cortes de tomosíntesis como si fueran mamografías 2D.
- Las fotos de pantalla con baja resolución solo usan el modelo global (sin mapa de calor).
