from flask import Flask, render_template, request, jsonify, Response
import yt_dlp
import os
import subprocess
import requests

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Rutas explícitas — no dependen del PATH de Windows
NODE_PATH  = r"C:\Program Files\nodejs\node.exe"
FFMPEG_DIR = r"C:\Users\hecto\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
FFMPEG_EXE = os.path.join(FFMPEG_DIR, "ffmpeg.exe")

# Opciones base para todas las llamadas a yt-dlp
YDL_OPTS_BASE = {
    "quiet": True,
    "ffmpeg_location": FFMPEG_DIR,
    "extractor_args": {
        "youtube": {
            "js_runtimes": [f"nodejs:{NODE_PATH}"]
        }
    }
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/proxy-image")
def proxy_image():
    """Proxy para descargar imágenes del storyboard evitando CORS."""
    url = request.args.get("url")
    if not url:
        return "URL requerida", 400
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.youtube.com/"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        return Response(
            resp.content,
            content_type=resp.headers.get("Content-Type", "image/webp"),
            status=resp.status_code
        )
    except Exception as e:
        return str(e), 500


@app.route("/info", methods=["POST"])
def get_info():
    """Obtiene los formatos disponibles y datos del storyboard."""
    url = request.json.get("url")
    if not url:
        return jsonify({"error": "URL no proporcionada"}), 400

    try:
        with yt_dlp.YoutubeDL({**YDL_OPTS_BASE}) as ydl:
            info = ydl.extract_info(url, download=False)

        # --- Formatos de video ---
        formatos = []
        seen = set()

        for f in info.get("formats", []):
            ext    = f.get("ext")
            height = f.get("height")
            vcodec = f.get("vcodec", "none")

            if vcodec != "none" and height:
                key = (height, ext)
                if key not in seen:
                    seen.add(key)
                    formatos.append({
                        "label": f"{height}p — {ext.upper()}",
                        "height": height,
                        "ext": ext,
                        "wmv": False
                    })

                key_wmv = (height, "wmv")
                if key_wmv not in seen:
                    seen.add(key_wmv)
                    formatos.append({
                        "label": f"{height}p — WMV (convertido)",
                        "height": height,
                        "ext": ext,
                        "wmv": True
                    })

        formatos.sort(key=lambda x: (x["height"], 0 if not x["wmv"] else -1), reverse=True)

        # --- Storyboard ---
        storyboard = None
        for f in info.get("formats", []):
            if f.get("format_id", "").startswith("sb") and f.get("url"):
                frags = f.get("fragments") or []
                frag_urls = []
                if frags:
                    base_url = f.get("url", "")
                    for fr in frags:
                        frag_url = fr.get("url") or fr.get("path", "")
                        # Si es relativa, combinar con base
                        if frag_url and not frag_url.startswith("http"):
                            frag_url = base_url.rsplit("/", 1)[0] + "/" + frag_url
                        if frag_url:
                            # Pasar por nuestro proxy
                            frag_urls.append(f"/proxy-image?url={requests.utils.quote(frag_url, safe='')}")

                # Si no hay fragmentos, usar la URL directa como una sola imagen
                if not frag_urls and f.get("url"):
                    frag_urls = [f"/proxy-image?url={requests.utils.quote(f['url'], safe='')}"]

                storyboard = {
                    "frag_urls":    frag_urls,
                    "rows":         f.get("rows", 10),
                    "columns":      f.get("columns", 10),
                    "frame_width":  f.get("width", 160),
                    "frame_height": f.get("height", 90),
                }
                break

        return jsonify({
            "title":      info.get("title"),
            "thumbnail":  info.get("thumbnail"),
            "duration":   info.get("duration"),
            "formats":    formatos,
            "storyboard": storyboard,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def seconds_to_hhmmss(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02}"


@app.route("/download", methods=["POST"])
def download():
    """Descarga el video con el formato seleccionado, con recorte opcional."""
    data    = request.json
    url     = data.get("url")
    height  = data.get("height")
    ext     = data.get("ext")
    is_wmv  = data.get("wmv", False)
    trim    = data.get("trim", False)
    start_s = data.get("start", 0)
    end_s   = data.get("end", None)

    if not url or not height or not ext:
        return jsonify({"error": "Faltan parámetros"}), 400

    try:
        # Paso 1: Descargar video completo en MP4
        temp_opts = {
            **YDL_OPTS_BASE,
            "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
            "outtmpl": f"{DOWNLOAD_FOLDER}/%(title)s_temp.%(ext)s",
            "merge_output_format": "mp4",
        }

        with yt_dlp.YoutubeDL(temp_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            temp_filename = ydl.prepare_filename(info)
            temp_filename = os.path.splitext(temp_filename)[0] + ".mp4"

        base_name = temp_filename.replace("_temp.mp4", "")

        # Paso 2: Recortar si se pidió
        if trim and end_s is not None:
            start_str   = seconds_to_hhmmss(start_s)
            duration    = end_s - start_s
            trimmed_mp4 = base_name + "_trimmed.mp4"
            subprocess.run([
                FFMPEG_EXE, "-i", temp_filename,
                "-ss", start_str, "-t", str(duration),
                "-c", "copy", trimmed_mp4, "-y"
            ], check=True)
            os.remove(temp_filename)
            source_file = trimmed_mp4
        else:
            source_file = temp_filename

        # Paso 3: Convertir al formato final
        if is_wmv:
            final_file = base_name + ("_trimmed.wmv" if trim else ".wmv")
            subprocess.run([
                FFMPEG_EXE, "-i", source_file,
                "-vcodec", "wmv2", "-acodec", "wmav2",
                "-b:v", "1000k", "-b:a", "128k",
                final_file, "-y"
            ], check=True)
            os.remove(source_file)

        elif ext != "mp4":
            final_file = base_name + ("_trimmed." if trim else ".") + ext
            subprocess.run([FFMPEG_EXE, "-i", source_file, final_file, "-y"], check=True)
            os.remove(source_file)

        else:
            final_file = base_name + ("_trimmed.mp4" if trim else ".mp4")
            if source_file != final_file:
                os.rename(source_file, final_file)

        return jsonify({
            "success": True,
            "message": f"✅ Descargado: {os.path.basename(final_file)}"
        })

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Error en FFmpeg: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)