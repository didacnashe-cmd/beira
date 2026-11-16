# Exponer Beira a Internet

Beira escucha en `127.0.0.1:8100` y así debe seguir: solo la máquina donde
corre puede hablarle. Para que tus contactos lleguen desde fuera, pon delante
algo que termine **TLS** y reenvíe a ese puerto local. Aquí tienes tres formas;
elige una.

| Opción | Necesitas | Abre puertos en tu máquina |
| --- | --- | --- |
| [Cloudflare Tunnel](#1-cloudflare-tunnel) | Dominio gestionado en Cloudflare | No |
| [Tailscale Funnel](#2-tailscale-funnel) | Cuenta de Tailscale | No |
| [Caddy con dominio propio](#3-caddy-con-dominio-propio-y-tls) | Dominio y IP pública | Sí, 80 y 443 |

En los ejemplos, `beira.example.com` es tu dominio; cámbialo por el tuyo.

## Antes de empezar: `BEIRA_PUBLIC_URL`

`BEIRA_PUBLIC_URL` es la URL pública **base**, con `https://` y sin `/mcp`:
Beira añade `/mcp` al imprimir las invitaciones. Con Docker, ponla en un `.env`
junto al `docker-compose.yml` y recrea el contenedor:

```bash
echo "BEIRA_PUBLIC_URL=https://beira.example.com" > .env
```

```bash
docker compose up -d
```

Después, `invite` imprimirá `https://beira.example.com/mcp`. Comprueba que
llega desde fuera (es la única ruta que no pide token):

```bash
curl -fsS https://beira.example.com/healthz
```

## 1. Cloudflare Tunnel

El demonio `cloudflared` abre una conexión saliente hacia Cloudflare; no hace
falta abrir ningún puerto.

1. Instala `cloudflared` en la máquina de Beira e inicia sesión:

   ```bash
   cloudflared tunnel login
   ```

2. Crea el túnel y asígnale el hostname:

   ```bash
   cloudflared tunnel create beira
   ```

   ```bash
   cloudflared tunnel route dns beira beira.example.com
   ```

3. Crea `~/.cloudflared/config.yml` apuntando al puerto local:

   ```yaml
   tunnel: beira
   credentials-file: /ruta/a/<id-del-tunel>.json
   ingress:
     - hostname: beira.example.com
       service: http://127.0.0.1:8100
     - service: http_status:404
   ```

4. Arráncalo como servicio para que sobreviva a reinicios:

   ```bash
   sudo cloudflared service install
   ```

   Si ya tienes un túnel (por ejemplo, para tu MCP), basta con añadir otra
   entrada `hostname` en su `ingress`, o un *public hostname* desde el panel
   de Cloudflare Zero Trust, apuntando a `http://127.0.0.1:8100`.

5. Fija `BEIRA_PUBLIC_URL=https://beira.example.com` como se explica arriba.

**Qué no hacer**

- No pongas Cloudflare Access delante de ese hostname: los clientes MCP de tus
  contactos no podrían pasarlo (los Service Tokens de Access aún no están
  soportados). La autenticación la hace Beira con el token Bearer.
- No apuntes el túnel a la IP pública de la máquina ni a `0.0.0.0`; siempre a
  `127.0.0.1:8100`.

## 2. Tailscale Funnel

Funnel publica un puerto local en `https://<máquina>.<tailnet>.ts.net` con
certificado de Tailscale, sin abrir puertos.

1. Instala Tailscale en la máquina de Beira y únela a tu tailnet:

   ```bash
   sudo tailscale up
   ```

2. En la consola de Tailscale, activa HTTPS para la tailnet y permite Funnel a
   esa máquina (atributo `funnel` en la política de acceso). Si falta algo, el
   comando del paso siguiente te dirá qué y te dará el enlace.

3. Publica el puerto local en segundo plano:

   ```bash
   sudo tailscale funnel --bg 8100
   ```

   ```bash
   tailscale funnel status
   ```

   El estado muestra la URL pública, por ejemplo
   `https://beira-vps.tu-tailnet.ts.net`.

4. Fija `BEIRA_PUBLIC_URL` con esa URL exacta, sin `/mcp`.

Para dejar de publicarlo:

```bash
sudo tailscale funnel reset
```

**Qué no hacer**

- No uses `tailscale serve` si tus contactos no están en tu tailnet: `serve`
  solo publica dentro de ella; para Internet hace falta `funnel`.
- No cambies `BEIRA_HOST` a `0.0.0.0` ni a la IP de Tailscale para "facilitar"
  el acceso: Funnel ya reenvía a `127.0.0.1`.

## 3. Caddy con dominio propio y TLS

Caddy pide y renueva solo el certificado de Let's Encrypt. Necesitas una IP
pública y abrir los puertos 80 y 443 (no el 8100).

1. Crea un registro DNS `A` (y `AAAA` si usas IPv6) de `beira.example.com`
   hacia la IP pública de la máquina.
2. Abre los puertos 80 y 443 en el cortafuegos de la máquina y del proveedor.
   El 80 solo hace falta para emitir el certificado y redirigir a HTTPS.
3. Instala Caddy y pon esto en `/etc/caddy/Caddyfile`:

   ```caddy
   beira.example.com {
       reverse_proxy 127.0.0.1:8100
   }
   ```

4. Recarga Caddy:

   ```bash
   sudo systemctl reload caddy
   ```

   La primera petición puede tardar unos segundos mientras emite el
   certificado. Caddy reenvía bien las respuestas en streaming de MCP sin
   configuración extra.

5. Fija `BEIRA_PUBLIC_URL=https://beira.example.com`.

**Qué no hacer**

- No abras el puerto 8100 en el cortafuegos ni en el proveedor: todo el
  tráfico externo debe entrar por Caddy.
- No sirvas el sitio como `http://beira.example.com` ni desactives TLS: el
  token Bearer viajaría en claro.

## Reglas para cualquier opción

- **Nunca expongas `0.0.0.0`.** Deja `BEIRA_HOST=127.0.0.1` (el valor por
  defecto). Con `network_mode: host`, cambiarlo publicaría Beira en todas las
  interfaces de la máquina.
- **Nunca abras el puerto sin TLS.** Cada petición lleva el token del contacto
  en la cabecera `Authorization`; sin HTTPS cualquiera en el camino lo lee.
- **Una sola puerta.** Usa una de estas opciones, no varias a la vez: cada
  entrada extra es una superficie más que vigilar.
- **Revisa la auditoría** tras publicar (`python -m beira audit`): solo deberían
  aparecer los contactos que has invitado.
